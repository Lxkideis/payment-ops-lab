CREATE SCHEMA IF NOT EXISTS payops;
SET search_path TO payops, public;

CREATE TABLE IF NOT EXISTS currency (
  code text PRIMARY KEY CHECK (code ~ '^[A-Z]{3}$'),
  exponent smallint NOT NULL CHECK (exponent BETWEEN 0 AND 3)
);
INSERT INTO currency VALUES ('SGD',2),('USD',2),('JPY',0) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS ingest_batch (
  id uuid PRIMARY KEY, source text NOT NULL CHECK(source IN ('ledger','partner')),
  sha256 text NOT NULL CHECK(length(sha256)=64), row_count integer NOT NULL,
  imported_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(source,sha256)
);
CREATE TABLE IF NOT EXISTS ledger_entry (
  event_id text PRIMARY KEY, payment_id text NOT NULL,
  kind text NOT NULL CHECK(kind IN ('charge','refund')),
  currency text NOT NULL REFERENCES currency(code),
  gross_minor bigint NOT NULL CHECK(abs(gross_minor)<=9000000000000),
  fee_minor bigint NOT NULL CHECK(fee_minor>=0),
  net_minor bigint GENERATED ALWAYS AS (gross_minor-fee_minor) STORED,
  occurred_at date NOT NULL, expected_on date NOT NULL,
  batch_id uuid NOT NULL REFERENCES ingest_batch(id),
  CHECK ((kind='charge' AND gross_minor>0 AND fee_minor<gross_minor)
      OR (kind='refund' AND gross_minor<0 AND fee_minor=0)),
  CHECK(expected_on>=occurred_at)
);
CREATE TABLE IF NOT EXISTS settlement_entry (
  row_id text PRIMARY KEY, event_id text NOT NULL, installment integer NOT NULL CHECK(installment>0),
  currency text NOT NULL REFERENCES currency(code),
  gross_minor bigint NOT NULL CHECK(abs(gross_minor)<=9000000000000),
  fee_minor bigint NOT NULL CHECK(fee_minor>=0),
  net_minor bigint GENERATED ALWAYS AS (gross_minor-fee_minor) STORED,
  payout_id text NOT NULL, settled_on date NOT NULL,
  batch_id uuid NOT NULL REFERENCES ingest_batch(id)
  -- event_id deliberately has no ledger FK: external orphan records must be inspectable.
  -- Repeated business installment keys are retained for reconciliation, not silently removed.
);
CREATE INDEX IF NOT EXISTS settlement_event_idx ON settlement_entry(event_id,settled_on);
CREATE INDEX IF NOT EXISTS ledger_expected_idx ON ledger_entry(expected_on,currency);

CREATE TABLE IF NOT EXISTS recon_run (
  id uuid PRIMARY KEY, as_of date NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  completed_at timestamptz, duration_ms numeric(12,3), counts jsonb,
  ledger_rows integer NOT NULL, partner_rows integer NOT NULL
);
CREATE TABLE IF NOT EXISTS recon_item (
  run_id uuid NOT NULL REFERENCES recon_run(id), item_key text NOT NULL,
  payment_id text, ledger_currency text REFERENCES currency(code), partner_currency text,
  ledger_net bigint, partner_net bigint, delta_minor bigint,
  reason text NOT NULL CHECK(reason IN ('matched','pending','missing','amount','currency','duplicate','orphan')),
  expected_on date, partner_parts integer NOT NULL, payout_ids text,
  PRIMARY KEY(run_id,item_key)
);
CREATE INDEX IF NOT EXISTS recon_item_filter_idx ON recon_item(run_id,reason,ledger_currency);
CREATE TABLE IF NOT EXISTS case_record (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), item_key text NOT NULL, reason text NOT NULL,
  status text NOT NULL DEFAULT 'open' CHECK(status IN ('open','investigating','resolved')),
  version integer NOT NULL DEFAULT 1, owner text,
  first_seen timestamptz NOT NULL DEFAULT clock_timestamp(),
  last_seen timestamptz NOT NULL DEFAULT clock_timestamp(),
  last_run uuid NOT NULL REFERENCES recon_run(id),
  UNIQUE(item_key,reason)
);
CREATE INDEX IF NOT EXISTS case_queue_idx ON case_record(last_run,status,reason);
CREATE TABLE IF NOT EXISTS audit_event (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), case_id uuid NOT NULL REFERENCES case_record(id),
  actor text NOT NULL, actor_role text NOT NULL, from_status text NOT NULL, to_status text NOT NULL,
  note text NOT NULL CHECK(length(note)>=8), version integer NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(case_id,version)
);
CREATE OR REPLACE FUNCTION reject_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Audit events are append-only'; END $$;
DROP TRIGGER IF EXISTS audit_is_append_only ON audit_event;
CREATE TRIGGER audit_is_append_only BEFORE UPDATE OR DELETE ON audit_event
  FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation();

GRANT USAGE ON SCHEMA payops TO portfolio_app;
GRANT SELECT ON ALL TABLES IN SCHEMA payops TO portfolio_app;
GRANT INSERT,UPDATE ON recon_run,case_record TO portfolio_app;
GRANT INSERT ON ingest_batch,ledger_entry,settlement_entry,recon_item,audit_event TO portfolio_app;
REVOKE UPDATE ON ingest_batch FROM portfolio_app;
