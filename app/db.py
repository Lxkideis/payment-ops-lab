import json,os
from contextlib import contextmanager
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
ROOT=Path(__file__).resolve().parents[1]

def database_url():
    if os.getenv('PAYOPS_DATABASE_URL'):return os.environ['PAYOPS_DATABASE_URL']
    state=ROOT/'_runtime/state.json'
    if not state.exists():raise RuntimeError('Run: python scripts/local_db.py start')
    return json.loads(state.read_text())['app_url']

@contextmanager
def connection():
    with psycopg.connect(database_url(),row_factory=dict_row,connect_timeout=3) as conn:
        conn.execute("SET statement_timeout TO '15s'")
        conn.execute("SET search_path TO payops, public")
        yield conn

def pipeline_lock(conn):
    # A deliberate small-lab tradeoff: serialize ingestion and snapshots for consistent runs.
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('payops_pipeline'))")
