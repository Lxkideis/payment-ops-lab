from pathlib import Path
from urllib.parse import urlsplit
from datetime import date
import json,sys
import psycopg,pytest
from fastapi.testclient import TestClient
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.main import app
from app.ingest import ingest
from app.reconciliation import reconcile
from scripts.generate_data import generate

@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    state=json.loads((ROOT/'_runtime/state.json').read_text())
    target=urlsplit(state['test_owner_url'])
    assert target.path=='/payops_test' and target.hostname=='127.0.0.1'
    monkeypatch.setenv('PAYOPS_DATABASE_URL',state['test_url'])
    with psycopg.connect(state['test_owner_url']) as c:
        c.execute('''TRUNCATE payops.audit_event,payops.case_record,payops.recon_item,payops.recon_run,
          payops.settlement_entry,payops.ledger_entry,payops.ingest_batch CASCADE''')

@pytest.fixture
def client():
    with TestClient(app) as c:yield c

@pytest.fixture
def sample(tmp_path):
    manifest=generate(tmp_path,count=600)
    return manifest,{s:(tmp_path/f'{s}.csv').read_bytes() for s in ['ledger','partner']}

@pytest.fixture
def seeded(sample):
    manifest,bodies=sample
    for s,body in bodies.items():ingest(s,body)
    run=reconcile(date.fromisoformat(manifest['as_of']))
    return manifest,bodies,run

@pytest.fixture
def analyst():return {'Authorization':'Bearer demo-analyst-token'}

@pytest.fixture
def reviewer():return {'Authorization':'Bearer demo-reviewer-token'}
