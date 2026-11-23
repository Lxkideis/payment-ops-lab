from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import UUID
import csv,io,os,secrets,time
from fastapi import FastAPI,Depends,HTTPException,Query,Request
from fastapi.responses import FileResponse,PlainTextResponse,Response
from fastapi.security import HTTPAuthorizationCredentials,HTTPBearer
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel,Field
from .db import connection
from .ingest import ingest,ImportRejected
from .reconciliation import reconcile,update_case,CaseConflict,CasePermission

ROOT=Path(__file__).resolve().parents[1]
app=FastAPI(title='Payment Operations Lab',version='0.1.0',description='Synthetic payment reconciliation portfolio. No real money or customer data.')
app.mount('/static',StaticFiles(directory=ROOT/'web'),name='static')
bearer=HTTPBearer(auto_error=False)
requests=Counter();latency=Counter()

@app.middleware('http')
async def measure(request:Request,call_next):
    started=time.perf_counter();response=await call_next(request)
    route=request.scope.get('route');path=getattr(route,'path','other')
    key=(request.method,path,str(response.status_code));requests[key]+=1
    latency[(request.method,path)]+=time.perf_counter()-started
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    return response

def operator(cred:HTTPAuthorizationCredentials|None=Depends(bearer)):
    if cred:
        for role,env,default in [('analyst','PAYOPS_ANALYST_TOKEN','demo-analyst-token'),('reviewer','PAYOPS_REVIEWER_TOKEN','demo-reviewer-token')]:
            if secrets.compare_digest(cred.credentials,os.getenv(env,default)):return {'actor':f'demo-{role}','role':role}
    raise HTTPException(401,'A demo operator token is required')

def latest(conn):
    row=conn.execute('SELECT * FROM recon_run WHERE completed_at IS NOT NULL ORDER BY created_at DESC LIMIT 1').fetchone()
    if not row:raise HTTPException(404,'No reconciliation yet. Import data and run a snapshot.')
    return row

def money(value,exponent):
    return f'{Decimal(value or 0)/(10**exponent):,.{exponent}f}'

@app.get('/',include_in_schema=False)
def home():return FileResponse(ROOT/'web/index.html')

@app.get('/health/live')
def live():return {'status':'live'}

@app.get('/health/ready')
def ready():
    try:
        with connection() as c:c.execute('SELECT 1')
        return {'status':'ready','database':'PostgreSQL'}
    except Exception:return Response('Database unavailable',status_code=503,media_type='text/plain')

@app.get('/metrics',response_class=PlainTextResponse)
def metrics():
    lines=['# TYPE payops_http_requests_total counter']
    for (method,path,status),count in sorted(requests.items()):
        lines.append(f'payops_http_requests_total{{method="{method}",route="{path}",status="{status}"}} {count}')
    lines.append('# TYPE payops_http_duration_seconds_sum counter')
    for (method,path),seconds in sorted(latency.items()):lines.append(f'payops_http_duration_seconds_sum{{method="{method}",route="{path}"}} {seconds:.6f}')
    return '\n'.join(lines)+'\n'

@app.post('/api/ingest/{source}')
async def import_csv(source:Literal['ledger','partner'],request:Request,user=Depends(operator)):
    # Stream-size guard: do not buffer an unbounded upload before validation.
    chunks=[];size=0
    async for chunk in request.stream():
        size+=len(chunk)
        if size>20_000_000:raise HTTPException(413,'CSV limit is 20 MB')
        chunks.append(chunk)
    try:return await run_in_threadpool(ingest,source,b''.join(chunks))
    except ImportRejected as e:raise HTTPException(422,str(e)) from e

class RunRequest(BaseModel):as_of:date

@app.post('/api/reconcile')
def run_snapshot(body:RunRequest,user=Depends(operator)):
    try:return reconcile(body.as_of)
    except ValueError as e:raise HTTPException(422,str(e)) from e

@app.get('/api/summary')
def summary(currency:Literal['SGD','USD','JPY']='SGD'):
    with connection() as c:
        run=latest(c);rid=run['id'];exp=c.execute('SELECT exponent FROM currency WHERE code=%s',(currency,)).fetchone()['exponent']
        rows=c.execute('''SELECT reason,count(*) n FROM recon_item WHERE run_id=%s
          AND coalesce(ledger_currency,partner_currency)=%s GROUP BY reason''',(rid,currency)).fetchall()
        counts={r['reason']:r['n'] for r in rows}
        eligible=sum(v for k,v in counts.items() if k not in ['pending','orphan'])
        matched=counts.get('matched',0)
        amount=c.execute('''SELECT coalesce(sum(abs(ledger_net)),0) n FROM recon_item WHERE run_id=%s
          AND ledger_currency=%s AND reason NOT IN ('matched','pending')''',(rid,currency)).fetchone()['n']
        aging=c.execute('''SELECT CASE WHEN %s-expected_on<=2 THEN '0-2 days' WHEN %s-expected_on<=7 THEN '3-7 days'
          WHEN %s-expected_on<=30 THEN '8-30 days' ELSE '31+ days' END bucket,
          count(*) n,sum(abs(ledger_net)) amount_minor FROM recon_item WHERE run_id=%s AND ledger_currency=%s
          AND reason NOT IN ('matched','pending') GROUP BY 1''',(run['as_of'],run['as_of'],run['as_of'],rid,currency)).fetchall()
        statuses=c.execute('''SELECT cr.status,count(*) n FROM case_record cr JOIN recon_item ri
          ON ri.run_id=cr.last_run AND ri.item_key=cr.item_key AND ri.reason=cr.reason
          WHERE cr.last_run=%s AND coalesce(ri.ledger_currency,ri.partner_currency)=%s GROUP BY cr.status''',(rid,currency)).fetchall()
        history=c.execute('SELECT id,created_at,as_of,duration_ms,counts FROM recon_run WHERE completed_at IS NOT NULL ORDER BY created_at DESC LIMIT 8').fetchall()
        sources=c.execute('SELECT source,sha256,row_count,imported_at FROM ingest_batch ORDER BY imported_at DESC LIMIT 8').fetchall()
        actual=c.execute('SELECT count(*) n FROM recon_item WHERE run_id=%s',(rid,)).fetchone()['n']
        expected=c.execute('''SELECT (SELECT count(*) FROM ledger_entry WHERE occurred_at<=%s)
          +(SELECT count(DISTINCT p.event_id) FROM settlement_entry p WHERE p.settled_on<=%s
            AND NOT EXISTS(SELECT 1 FROM ledger_entry l WHERE l.event_id=p.event_id)) n''',
          (run['as_of'],run['as_of'])).fetchone()['n']
        money_violations=c.execute('''SELECT count(*) n FROM recon_item WHERE run_id=%s AND
          ((reason='matched' AND (ledger_net IS DISTINCT FROM partner_net
            OR ledger_currency IS DISTINCT FROM partner_currency OR delta_minor IS DISTINCT FROM 0))
          OR (reason IN ('currency','duplicate') AND delta_minor IS NOT NULL))''',(rid,)).fetchone()['n']
        return {'currency':currency,'exponent':exp,'run':run,'counts':counts,'eligible_ledger_entries':eligible,
          'matched':matched,'match_rate':round(matched/eligible*100,3) if eligible else 0,
          'exception_count':sum(v for k,v in counts.items() if k not in ['matched','pending']),
          'flagged_ledger_minor':str(amount),'flagged_ledger_display':money(amount,exp),
          'aging':[{**r,'amount_minor':str(r['amount_minor']),'display':money(r['amount_minor'],exp)} for r in aging],
          'case_statuses':{r['status']:r['n'] for r in statuses},'history':history,'sources':sources,
          'controls':[{'name':'Snapshot accounting','passed':actual==expected==sum(run['counts'].values()),'detail':f'{actual:,} items / {expected:,} expected events'},
            {'name':'Money control checks','passed':money_violations==0,'detail':f'{money_violations} matched-net / quarantined-delta violations'},
            {'name':'Raw input lineage','passed':bool(sources),'detail':'SHA-256 and import batches recorded'}]}

CaseReason=Literal['all','missing','amount','currency','duplicate','orphan']
CaseStatus=Literal['all','open','investigating','resolved']

def case_query(c,currency,reason,status,page,limit):
    run=latest(c)
    base=''' FROM case_record cr JOIN recon_item ri ON ri.run_id=cr.last_run AND ri.item_key=cr.item_key AND ri.reason=cr.reason
      WHERE cr.last_run=%s AND coalesce(ri.ledger_currency,ri.partner_currency)=%s
      AND (%s='all' OR cr.reason=%s) AND (%s='all' OR cr.status=%s)'''
    args=[run['id'],currency,reason,reason,status,status]
    total=c.execute('SELECT count(*) n'+base,args).fetchone()['n']
    rows=c.execute('''SELECT cr.*,ri.payment_id,ri.ledger_currency,ri.partner_currency,ri.ledger_net,ri.partner_net,
      ri.delta_minor,ri.expected_on,ri.partner_parts,ri.payout_ids'''+base+'''
      ORDER BY CASE cr.reason WHEN 'currency' THEN 0 WHEN 'duplicate' THEN 1 ELSE 2 END,
      ri.expected_on NULLS LAST,cr.item_key LIMIT %s OFFSET %s''',args+[limit,(page-1)*limit]).fetchall()
    return {'total':total,'page':page,'limit':limit,'rows':rows,'as_of':run['as_of']}

@app.get('/api/cases')
def cases(currency:Literal['SGD','USD','JPY']='SGD',reason:CaseReason='all',status:CaseStatus='all',
          page:int=Query(1,ge=1),limit:int=Query(15,ge=1,le=100)):
    with connection() as c:return case_query(c,currency,reason,status,page,limit)

@app.get('/api/cases/{case_id}')
def case_detail(case_id:UUID):
    with connection() as c:
        row=c.execute('''SELECT cr.*,ri.payment_id,ri.ledger_currency,ri.partner_currency,ri.ledger_net,
          ri.partner_net,ri.delta_minor,ri.partner_parts,ri.payout_ids,ri.expected_on FROM case_record cr
          JOIN recon_item ri ON ri.run_id=cr.last_run AND ri.item_key=cr.item_key AND ri.reason=cr.reason WHERE cr.id=%s''',(case_id,)).fetchone()
        if not row:raise HTTPException(404,'Case not found')
        ledger=c.execute('SELECT event_id,payment_id,kind,currency,gross_minor,fee_minor,net_minor,occurred_at,expected_on,batch_id FROM ledger_entry WHERE event_id=%s',(row['item_key'],)).fetchone()
        partner=c.execute('SELECT row_id,event_id,installment,currency,gross_minor,fee_minor,net_minor,payout_id,settled_on,batch_id FROM settlement_entry WHERE event_id=%s ORDER BY row_id',(row['item_key'],)).fetchall()
        audit=c.execute('SELECT actor,actor_role,from_status,to_status,note,version,created_at FROM audit_event WHERE case_id=%s ORDER BY version',(case_id,)).fetchall()
        return {'case':row,'ledger':ledger,'partner':partner,'audit':audit}

class CaseUpdate(BaseModel):
    expected_version:int=Field(ge=1)
    status:Literal['investigating','resolved']
    note:str=Field(min_length=8,max_length=2000)
    owner:str|None=Field(default=None,max_length=80)

@app.patch('/api/cases/{case_id}')
def patch_case(case_id:UUID,body:CaseUpdate,user=Depends(operator)):
    try:return update_case(case_id,body.expected_version,body.status,body.note,user['actor'],user['role'],body.owner)
    except CaseConflict as e:raise HTTPException(409,str(e)) from e
    except CasePermission as e:raise HTTPException(403,str(e)) from e
    except LookupError as e:raise HTTPException(404,str(e)) from e
    except ValueError as e:raise HTTPException(422,str(e)) from e

def safe_csv_cell(value):
    if isinstance(value,str) and value.startswith(('=','+','-','@','\t','\r')):return "'"+value
    return value

@app.get('/api/export.csv')
def export(currency:Literal['SGD','USD','JPY']='SGD',reason:CaseReason='all',status:CaseStatus='all'):
    with connection() as c:data=case_query(c,currency,reason,status,1,100000)
    out=io.StringIO(newline='');fields=['item_key','reason','status','version','owner','ledger_currency','partner_currency','ledger_net','partner_net','delta_minor','expected_on']
    writer=csv.writer(out);writer.writerow(fields)
    for row in data['rows']:writer.writerow([safe_csv_cell(row.get(f)) for f in fields])
    return Response(out.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="payops_{currency}_exceptions.csv"'})
