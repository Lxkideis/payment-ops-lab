"""Small, explicitly warm local experiment. This is not a production load test."""
from pathlib import Path
from datetime import date,datetime,timezone
import json,platform,statistics,sys,time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.db import connection
from app.reconciliation import reconcile,SQL

ROOT=Path(__file__).resolve().parents[1]

def main():
    manifest=json.loads((ROOT/'data/manifest.json').read_text())
    as_of=date.fromisoformat(manifest['as_of'])
    with connection() as c:
        version=c.execute('SHOW server_version').fetchone()['server_version']
        plan=c.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+SQL,{'as_of':as_of}).fetchone()['QUERY PLAN']
    (ROOT/'artifacts/query-plan.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
    reconcile(as_of)  # Explicitly exclude one warm-up.
    wall=[];engine=[]
    for _ in range(7):
        begin=time.perf_counter();r=reconcile(as_of);wall.append((time.perf_counter()-begin)*1000)
        engine.append(r['duration_ms'])
        assert r['counts']==manifest['expected_reconciliation_counts']
    result={'recorded_utc':datetime.now(timezone.utc).isoformat(),'python':platform.python_version(),
      'os':platform.system()+' '+platform.release(),'postgresql':version,
      'ledger_rows':r['ledger_rows'],'partner_rows':r['partner_rows'],'repetitions':7,
      'wall_ms_including_connection_and_commit':[round(x,3) for x in wall],
      'median_wall_ms':round(statistics.median(wall),3),'min_wall_ms':round(min(wall),3),
      'max_wall_ms':round(max(wall),3),'engine_ms_before_commit':engine,
      'all_classifications_match_oracle':True,
      'conditions':'One local Windows machine; native PostgreSQL; one excluded warm-up; warm caches; sequential runs; no concurrent workload. Query-plan is SELECT-only and has different scope from full reconciliation.',
      'limits':'No before/after speedup claim, p95 claim, production capacity claim, or business impact estimate.'}
    (ROOT/'artifacts/local-benchmark.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
