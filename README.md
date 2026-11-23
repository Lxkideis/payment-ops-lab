# Clearset · Payment Operations Lab

A working local portfolio application for investigating payment-data exceptions. It uses **entirely synthetic data**, not Stripe data, and has no payment or bank connection.

Independent demonstration application developed with AI assistance in October 2026. All economic records are synthetic; this is not an employer assignment or a production financial system.

## What to try

1. Open the overview and compare SGD, USD and JPY. No cross-currency money total is produced.
2. Inspect a currency mismatch: the ledger and partner amounts have different currencies, so the system deliberately leaves the difference blank.
3. Filter missing settlements, amount differences, repeated installments or external orphans; paginate and export the filtered queue.
4. Start an investigation with an evidence note. The analyst cannot resolve a case; the reviewer can. A stale version returns HTTP 409.
5. Open Sources & runs. Import the same provided CSV again: its SHA-256 reuses the original batch. A changed file with a conflicting source ID is rejected atomically.
6. Run another snapshot. Pending entries are distinct from missing settlements; workflow resolution never changes financial matching.

## Run on this Windows machine

Prerequisites: Python 3.11+ and native PostgreSQL 18 binaries. The current machine has PostgreSQL at `C:\Program Files\PostgreSQL\18\bin`. Override with `PAYOPS_PG_BIN` if needed. This Windows launcher has been exercised locally; other platforms are not validated.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\run_local.ps1
```

If the bundled Python has no pip inside the venv, use an available pip runner:
`python -m pip --python .venv\Scripts\python.exe install -r requirements-lock.txt`.
Use a PowerShell session that permits local scripts, or run the three Python commands in `run_local.ps1` directly. No machine-wide execution-policy change is required.

`scripts/local_db.py start` initializes only `_runtime/pgdata`, binds PostgreSQL to **127.0.0.1 on an available port**, and creates `payops_demo` and `payops_test`. It does not change a Windows service or the user's existing databases. Locally generated database passwords stay in ignored `_runtime/state.json`; never commit that directory. The application account has no DDL or source-row update/delete privileges. The bootstrap owner is used only for setup and isolated test cleanup.

Ctrl+C stops Uvicorn. To stop the project's PostgreSQL while retaining its data:
`.\.venv\Scripts\python.exe scripts/local_db.py stop`.
Restart with `run_local.ps1`. The API is at `/docs`; readiness is `/health/ready`, liveness `/health/live`, and process-local HTTP counters `/metrics`.

## Reproduce the evidence

```powershell
.\.venv\Scripts\python.exe -m pytest -q --junitxml=artifacts/test-results.xml
.\.venv\Scripts\python.exe scripts/benchmark.py
```

Tests truncate **only the named loopback `payops_test` database** after validating its URL. They do not clear the demo. Cases cover exact refund/split arithmetic, currency and duplicate quarantine, missing versus not-yet-due, atomic rejection, concurrent idempotent imports, concurrent stale updates, reviewer authorization, append-only audit access and CSV export safety. A dependency deprecation warning may be emitted by Starlette's current TestClient/httpx combination; the recorded test result is separate from that warning.

Default seed: 10,200 ledger entries (including 200 refunds) and 10,212 partner rows, as of **2026-10-21**. The generator records hashes and an independently constructed expected classification in `data/manifest.json`. `seed_demo.py` checks the database result against it:

| Classification | Events |
|---|---:|
| Matched | 9,900 |
| Missing | 50 |
| Amount mismatch | 100 |
| Currency mismatch | 50 |
| Duplicate installment | 50 |
| Pending | 50 |
| Orphan | 12 |

There are **262 actionable exception events**, not 262 proven losses. The 50 pending events are separate. The sum is 10,212 classified event keys; partner rows and event keys are different grains. Fifty valid split events demonstrate why partner rows must be aggregated before joining. Generator ground truth validates these scenarios, not every possible financial system behavior.

`artifacts/local-benchmark.json` records seven sequential warm-cache local runs and wall time including connection and commit. `recon_run.duration_ms` measures the engine up to its final update, before commit. `artifacts/query-plan.json` is a separate SELECT-only EXPLAIN ANALYZE with buffers. No production scale or optimization speedup is inferred from these measurements.

## Design and stack

CSV → strict Python/Pydantic validation → transactional PostgreSQL COPY → SQL aggregation/full outer join → immutable result items → versioned investigation cases → FastAPI → plain JavaScript dashboard.

- **Python / FastAPI / psycopg 3**: validated ingestion, API errors and transactional workflow. Streaming input is capped before buffering; synchronous ingestion runs in a thread pool.
- **PostgreSQL 18 / SQL**: primary/composite keys, foreign keys, checks, generated net amounts, CTEs, full outer join, row locks and a transaction advisory lock. The app retains source records and records batch lineage.
- **Vanilla JavaScript / CSS**: real API-driven filters, charts, drawer, export and history, with no external UI/CDN dependency. Values are escaped before HTML rendering.
- **pytest + actual PostgreSQL**: meaningful financial and concurrency checks, not mocked database behavior. HTTP tests use FastAPI TestClient.

### Why PostgreSQL rather than MySQL?

PostgreSQL supports this implementation's full outer join, COPY, generated columns and locking directly. Exact money arithmetic, explicit data grain and transactional workflow determine the design. A MySQL 8 port would need a union-based full join, driver/COPY replacement and reviewed locking/migration behavior; it is not claimed to have been implemented or tested. SQL data-grain and money-control reasoning transfer beyond either database.

### Money and matching rules

All source money is signed integer minor units. SGD/USD use 2 decimals and JPY uses 0. Reconciliation compares **net** after modeled fees; it does not separately reconcile fee/gross components. Partner splits are grouped by event first. Duplicate business installment keys and mixed currencies are quarantined; a meaningless cross-currency delta is never shown. External records intentionally have no ledger-event FK so orphans remain inspectable.

The match-rate denominator is eligible ledger events: all ledger entries in the snapshot except unsettled entries whose expected date is still in the future. Orphans are excluded. Early settled events can therefore be eligible before their expected date. Flagged ledger amount is the sum of absolute net amounts for exceptions with a ledger record in the selected currency, **not cash lost, net balance or a liability estimate**. Orphan exposure has no ledger amount and is omitted from that metric. Aging groups flagged ledger exposure by expected date. No FX conversion is modeled.

`recon_item` preserves result rows; `case_record` tracks review state separately. Source and snapshot writes serialize through one advisory transaction lock for consistency at this small scale. Case decisions use row locking plus a client version and append an audit row in the same transaction. The app cannot update/delete audit rows; an owner/superuser can still administer the database, so this is not a tamper-proof audit system.

## Scope and limitations

This is an event-to-partner **two-source reconciliation**, not a full general ledger or bank-payout reconciliation. Payout IDs are contextual fields, not independent bank evidence. Refunds are fee-free in this simplified model. Source corrections require a designed future adjustment/versioning flow; conflicting canonical IDs currently reject rather than overwrite. There is no queue-worker architecture, connection pool, scalable scheduler, formal migration framework, SSO, distributed tracing, real client adoption or measured business savings. The single pipeline lock and full snapshots trade throughput/storage for simpler consistency.

The default UI exposes known **local demo tokens** for analyst/reviewer. Reads are unauthenticated and all data is synthetic. Role selection is an educational demonstration of server-enforced permissions, not production identity. Do not publicly expose this configuration. Real deployment would require identity, secrets, TLS, access controls, request limits, backup/restore validation and operational design.

## Files to review

- `sql/001_schema.sql`: schema and privilege choices.
- `sql/reconcile.sql`: classification and data grain.
- `app/ingest.py`: strict validation, idempotence, atomic COPY.
- `app/reconciliation.py`: snapshots and case transitions.
- `tests/`: financial, concurrency and HTTP evidence.

Context sources: [Stripe reconciliation concepts](https://docs.stripe.com/reports/payout-reconciliation), [PostgreSQL locking](https://www.postgresql.org/docs/18/explicit-locking.html), [FastAPI testing](https://fastapi.tiangolo.com/tutorial/testing/). The application is an original synthetic exercise, not an implementation of Stripe's API or its complete financial model.

## Related work

[Clearset Close](https://github.com/Lxkideis/clearset-close) extends the financial-control scope to seven sources, independent gross/fee/net checks and bank evidence. [Data Operations Labs](https://github.com/Lxkideis/data-operations-labs) contains five smaller cases.

## Generated inputs

Synthetic source CSVs and their manifest are generated deterministically on first startup by `scripts.seed_demo`. They are not committed. Saved evidence summaries remain under `artifacts/`; the generator code defines the injected defects and independent expected counts.
