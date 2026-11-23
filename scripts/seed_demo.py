from pathlib import Path
import json,sys
from datetime import date
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.ingest import ingest
from app.reconciliation import reconcile
from scripts.generate_data import generate
ROOT=Path(__file__).resolve().parents[1]

def seed():
    if not (ROOT/'data/manifest.json').exists():generate()
    manifest=json.loads((ROOT/'data/manifest.json').read_text())
    imports={source:ingest(source,(ROOT/'data'/f'{source}.csv').read_bytes()) for source in ['ledger','partner']}
    result=reconcile(date.fromisoformat(manifest['as_of']))
    assert result['counts']==manifest['expected_reconciliation_counts'],(result,manifest)
    (ROOT/'artifacts').mkdir(exist_ok=True)
    (ROOT/'artifacts/seed_verification.json').write_text(json.dumps({'imports':imports,'run':result,'oracle_matches':True},indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
if __name__=='__main__':seed()
