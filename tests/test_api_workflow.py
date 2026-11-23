from concurrent.futures import ThreadPoolExecutor
import psycopg,pytest
from app.db import connection
from app.reconciliation import update_case,CaseConflict

def first_case(client):return client.get('/api/cases?currency=SGD').json()['rows'][0]

def test_role_gate_state_machine_and_immutable_audit(client,seeded,analyst,reviewer):
    row=first_case(client);url='/api/cases/'+row['id']
    body={'expected_version':1,'status':'investigating','note':'Reviewed source records and assigned an owner.'}
    assert client.patch(url,json=body).status_code==401
    assert client.patch(url,json={**body,'status':'resolved'},headers=reviewer).status_code==422
    assert client.patch(url,json=body,headers=analyst).status_code==200
    assert client.patch(url,json=body,headers=analyst).status_code==409
    resolve={'expected_version':2,'status':'resolved','note':'Review completed; discrepancy remains in reconciliation.'}
    assert client.patch(url,json=resolve,headers=analyst).status_code==403
    assert client.patch(url,json=resolve,headers=reviewer).status_code==200
    detail=client.get(url).json();assert len(detail['audit'])==2
    assert [a['version'] for a in detail['audit']]==[2,3]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with connection() as c:c.execute('UPDATE audit_event SET note=%s WHERE case_id=%s',('attempted edit',row['id']))

def test_concurrent_stale_edits_have_one_winner(client,seeded):
    row=first_case(client)
    def edit(n):
        try:update_case(row['id'],1,'investigating',f'Concurrent analyst note {n}','test-analyst','analyst');return 200
        except CaseConflict:return 409
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(edit,range(2)))
    assert sorted(results)==[200,409]
    assert len(client.get('/api/cases/'+row['id']).json()['audit'])==1

def test_workflow_resolution_never_changes_financial_match_rate(client,seeded,analyst,reviewer):
    before=client.get('/api/summary').json();row=first_case(client);url='/api/cases/'+row['id']
    for version,status,headers in [(1,'investigating',analyst),(2,'resolved',reviewer)]:
        assert client.patch(url,json={'expected_version':version,'status':status,'note':'Evidence reviewed in this synthetic example.'},headers=headers).status_code==200
    after=client.get('/api/summary').json()
    assert before['match_rate']==after['match_rate'] and before['counts']==after['counts']
    assert after['case_statuses']['resolved']==1

def test_filters_pagination_export_and_validation(client,seeded):
    q=client.get('/api/cases?currency=JPY&reason=amount&limit=2').json()
    assert q['total']>=2 and len(q['rows'])==2
    assert all(r['ledger_currency']=='JPY' and r['reason']=='amount' for r in q['rows'])
    assert client.get('/api/cases?limit=1000').status_code==422
    assert client.get('/api/cases?reason=%27%20OR%201=1--').status_code==422
    export=client.get('/api/export.csv?currency=JPY&reason=amount')
    assert export.status_code==200 and 'text/csv' in export.headers['content-type']
    assert 'JPY' in export.text and 'SGD' not in export.text

def test_replay_over_http_and_short_notes(client,seeded,analyst):
    _,bodies,_=seeded
    assert client.post('/api/ingest/ledger',content=bodies['ledger'],headers={**analyst,'Content-Type':'text/csv'}).json()['replayed']
    row=first_case(client)
    assert client.patch('/api/cases/'+row['id'],json={'expected_version':1,'status':'investigating','note':'x'},headers=analyst).status_code==422

def test_readiness_failure_is_visible_without_faking_liveness(client,monkeypatch):
    import app.main as module
    from contextlib import contextmanager
    @contextmanager
    def unavailable():raise OSError('test database failure');yield
    monkeypatch.setattr(module,'connection',unavailable)
    assert client.get('/health/live').status_code==200
    assert client.get('/health/ready').status_code==503

def test_summary_controls_and_openapi_are_real(client,seeded):
    s=client.get('/api/summary?currency=USD').json()
    assert s['currency']=='USD' and all(c['passed'] for c in s['controls'])
    assert s['flagged_ledger_minor'].isdigit()
    assert client.get('/openapi.json').status_code==200
    assert 'payops_http_requests_total' in client.get('/metrics').text


def test_snapshot_controls_detect_corruption_instead_of_always_passing(client,seeded):
    import json
    from pathlib import Path
    state=json.loads((Path(__file__).resolve().parents[1]/'_runtime/state.json').read_text())
    # Deliberate corruption is restricted to the disposable test database by the fixture.
    with psycopg.connect(state['test_owner_url']) as c:
        c.execute("UPDATE payops.recon_item SET delta_minor=7 WHERE reason='matched'")
    result=client.get('/api/summary').json()
    assert not next(x for x in result['controls'] if x['name']=='Money control checks')['passed']


def test_early_settlement_is_eligible_not_pending(client):
    from app.ingest import ingest
    from app.reconciliation import reconcile
    from datetime import date
    ingest('ledger',b'event_id,payment_id,kind,currency,gross_minor,fee_minor,occurred_at,expected_on\nearly,p1,charge,SGD,1000,20,2026-10-19,2026-10-25\n')
    ingest('partner',b'row_id,event_id,installment,currency,gross_minor,fee_minor,payout_id,settled_on\nr1,early,1,SGD,1000,20,po1,2026-10-20\n')
    reconcile(date(2026,10,21))
    result=client.get('/api/summary').json()
    assert result['match_rate']==100 and result['eligible_ledger_entries']==1
    assert result['counts']=={'matched':1}
