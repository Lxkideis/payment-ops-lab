-- One row per event. Aggregate partner parts BEFORE the full join.
-- Business-duplicate installments and cross-currency sums are quarantined.
WITH partner AS (
  SELECT event_id,count(*) AS parts,count(DISTINCT installment) AS unique_parts,
    count(DISTINCT currency) AS currencies,min(currency) AS currency,
    CASE WHEN count(DISTINCT currency)=1 THEN sum(net_minor) END AS net_minor,
    string_agg(DISTINCT payout_id,', ' ORDER BY payout_id) AS payout_ids
  FROM payops.settlement_entry WHERE settled_on <= %(as_of)s GROUP BY event_id
), compared AS (
  SELECT coalesce(l.event_id,p.event_id) AS item_key,l.payment_id,
    l.currency AS ledger_currency,p.currency AS partner_currency,
    l.net_minor AS ledger_net,p.net_minor AS partner_net,
    CASE WHEN l.currency=p.currency AND p.currencies=1 AND p.parts=p.unique_parts
      THEN l.net_minor-p.net_minor END AS delta_minor,
    CASE WHEN l.event_id IS NULL THEN 'orphan'
      WHEN p.event_id IS NULL AND l.expected_on>%(as_of)s THEN 'pending'
      WHEN p.event_id IS NULL THEN 'missing'
      WHEN p.parts>p.unique_parts THEN 'duplicate'
      WHEN p.currencies>1 OR l.currency<>p.currency THEN 'currency'
      WHEN l.net_minor<>p.net_minor THEN 'amount'
      ELSE 'matched' END AS reason,
    l.expected_on,coalesce(p.parts,0) AS partner_parts,p.payout_ids
  FROM payops.ledger_entry l FULL OUTER JOIN partner p ON l.event_id=p.event_id
  WHERE l.event_id IS NULL OR l.occurred_at<=%(as_of)s
)
SELECT * FROM compared
