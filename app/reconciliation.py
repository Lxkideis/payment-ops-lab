import json,time,uuid
from datetime import date
from pathlib import Path
from psycopg.types.json import Jsonb
from .db import connection,pipeline_lock
SQL=(Path(__file__).resolve().parents[1]/'sql/reconcile.sql').read_text()

def reconcile(as_of:date):
    started=time.perf_counter();rid=uuid.uuid4()
    with connection() as conn:
        pipeline_lock(conn)
        ledger=conn.execute('SELECT count(*) n FROM ledger_entry').fetchone()['n']
        partner=conn.execute('SELECT count(*) n FROM settlement_entry').fetchone()['n']
        if not ledger:raise ValueError('Import ledger data first')
        conn.execute('INSERT INTO recon_run(id,as_of,ledger_rows,partner_rows) VALUES(%s,%s,%s,%s)',(rid,as_of,ledger,partner))
        conn.execute('''INSERT INTO recon_item(run_id,item_key,payment_id,ledger_currency,partner_currency,
          ledger_net,partner_net,delta_minor,reason,expected_on,partner_parts,payout_ids)
          SELECT %(run_id)s,q.* FROM ('''+SQL+''') q''',{'as_of':as_of,'run_id':rid})
        counts={r['reason']:r['n'] for r in conn.execute('SELECT reason,count(*) n FROM recon_item WHERE run_id=%s GROUP BY reason',(rid,))}
        conn.execute('''INSERT INTO case_record(item_key,reason,last_run)
          SELECT item_key,reason,run_id FROM recon_item WHERE run_id=%s AND reason NOT IN ('matched','pending')
          ON CONFLICT(item_key,reason) DO UPDATE SET last_run=excluded.last_run,last_seen=clock_timestamp()''',(rid,))
        duration=round((time.perf_counter()-started)*1000,3)
        conn.execute('UPDATE recon_run SET completed_at=clock_timestamp(),duration_ms=%s,counts=%s WHERE id=%s',(duration,Jsonb(counts),rid))
    return {'id':str(rid),'as_of':str(as_of),'ledger_rows':ledger,'partner_rows':partner,'counts':counts,'duration_ms':duration}

class CaseConflict(ValueError):pass
class CasePermission(ValueError):pass

def update_case(case_id,expected_version,status,note,actor,role,owner=None):
    transitions={'open':{'investigating'},'investigating':{'resolved'},'resolved':{'investigating'}}
    if len(note.strip())<8:raise ValueError('Add an investigation note of at least 8 characters')
    if status=='resolved' and role!='reviewer':raise CasePermission('Only a reviewer can resolve a case')
    with connection() as conn:
        row=conn.execute('SELECT * FROM case_record WHERE id=%s FOR UPDATE',(case_id,)).fetchone()
        if not row:raise LookupError('Case not found')
        if row['version']!=expected_version:raise CaseConflict('Case changed; refresh before editing')
        if status not in transitions[row['status']]:raise ValueError('Invalid case transition')
        new=conn.execute('UPDATE case_record SET status=%s,version=version+1,owner=%s WHERE id=%s RETURNING *',(status,owner or actor,case_id)).fetchone()
        conn.execute('''INSERT INTO audit_event(case_id,actor,actor_role,from_status,to_status,note,version)
          VALUES(%s,%s,%s,%s,%s,%s,%s)''',(case_id,actor,role,row['status'],status,note.strip(),new['version']))
        return new
