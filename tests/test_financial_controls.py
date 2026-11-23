import csv,io
from datetime import date,timedelta
from concurrent.futures import ThreadPoolExecutor
import psycopg,pytest
from app.db import connection
from app.ingest import ingest,ImportRejected
from app.reconciliation import reconcile
from app.main import safe_csv_cell

def test_generated_oracle_matches_actual_postgres_full_join(seeded):
    manifest,_,run=seeded
    assert run['counts']==manifest['expected_reconciliation_counts']
    assert run['ledger_rows']==612

def test_exact_file_replay_is_idempotent(seeded):
    _,bodies,_=seeded
    assert ingest('ledger',bodies['ledger'])['replayed']
    with connection() as c:
        assert c.execute('SELECT count(*) n FROM ledger_entry').fetchone()['n']==612
        assert c.execute('SELECT count(*) n FROM ingest_batch').fetchone()['n']==2

def test_concurrent_identical_import_has_one_commit(sample):
    _,bodies=sample
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:ingest('ledger',bodies['ledger']),range(2)))
    assert sorted(r['replayed'] for r in results)==[False,True]
    assert results[0]['batch_id']==results[1]['batch_id']

def test_bad_row_rolls_back_entire_batch(sample):
    _,bodies=sample
    with pytest.raises(ImportRejected):ingest('ledger',bodies['ledger'].replace(b',SGD,',b',EUR,',1))
    with connection() as c:
        assert c.execute('SELECT count(*) n FROM ledger_entry').fetchone()['n']==0
        assert c.execute('SELECT count(*) n FROM ingest_batch').fetchone()['n']==0

def test_existing_source_key_cannot_silently_overwrite_money(seeded):
    _,bodies,_=seeded
    with pytest.raises(ImportRejected,match='rolled back'):ingest('ledger',bodies['ledger']+b'\n')
    with connection() as c:assert c.execute('SELECT count(*) n FROM ingest_batch').fetchone()['n']==2

def test_decimal_money_is_rejected_instead_of_rounded(sample):
    _,bodies=sample
    rows=list(csv.DictReader(io.StringIO(bodies['ledger'].decode())))
    rows[0]['gross_minor']='12.75';out=io.StringIO();w=csv.DictWriter(out,fieldnames=rows[0]);w.writeheader();w.writerows(rows[:2])
    with pytest.raises(ImportRejected,match='integer'):ingest('ledger',out.getvalue().encode())

def test_split_parts_and_refund_match_exactly(seeded):
    with connection() as c:
        rows=c.execute("SELECT * FROM recon_item WHERE item_key IN ('evt_000004','ref_000017') ORDER BY item_key").fetchall()
        assert all(r['reason']=='matched' and r['delta_minor']==0 for r in rows)
        assert rows[0]['partner_parts']==2 and rows[1]['ledger_net']<0

def test_currency_and_duplicate_cases_have_no_misleading_delta(seeded):
    with connection() as c:
        rows=c.execute("SELECT * FROM recon_item WHERE reason IN ('currency','duplicate')").fetchall()
        assert rows and all(r['delta_minor'] is None for r in rows)

def test_not_due_is_pending_then_becomes_missing(seeded):
    manifest,_,_=seeded
    later=reconcile(date.fromisoformat(manifest['as_of'])+timedelta(days=4))
    assert later['counts'].get('pending',0)==0
    assert later['counts']['missing']==manifest['expected_reconciliation_counts']['missing']+manifest['expected_reconciliation_counts']['pending']

def test_database_enforces_integrity_beyond_api(seeded):
    with pytest.raises(psycopg.errors.CheckViolation):
        with connection() as c:
            c.execute("INSERT INTO ledger_entry(event_id,payment_id,kind,currency,gross_minor,fee_minor,occurred_at,expected_on,batch_id) SELECT 'bad','bad','charge','SGD',-1,0,'2026-10-01','2026-10-02',id FROM ingest_batch LIMIT 1")

def test_csv_formula_strings_escaped_but_negative_numbers_preserved():
    assert safe_csv_cell('=HYPERLINK("x")').startswith("'")
    assert safe_csv_cell(-500)==-500 and safe_csv_cell('evt_001')=='evt_001'


def test_app_cannot_rewrite_import_lineage(seeded):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with connection() as c:c.execute("UPDATE ingest_batch SET sha256=repeat('a',64)")
