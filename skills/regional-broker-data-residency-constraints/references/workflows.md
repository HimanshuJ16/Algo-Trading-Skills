# Workflows for Regional Broker Data Residency Constraints

The deployment question splits into three independent axes. Keep them separate —
merging them is how a latency preference gets reported as a legal violation, and
how a real, enforced access control gets missed.

## 1. Resolve the deployment (never guess)

- Read the region from `AWS_REGION` / `AWS_DEFAULT_REGION`, then `GCP_REGION` /
  `CLOUD_RUN_REGION`, then `TRADING_HOST_REGION`. `probe_current_region()`
  returns `None` when none is set; it does not fall back to a default region.
- Resolve the region to a jurisdiction through the explicit map, not the name
  prefix. `eu-west-2` is London and `eu-central-2` is Zurich; `europe-west2` is
  London and `europe-west6` is Zurich. An unmapped region yields
  `REVIEW_REQUIRED`.
- Establish the **egress** posture, which is the property the broker actually
  sees. A region tells you nothing about whether the source address is stable.

## 2. Check the enforced broker access control

For SEBI-regulated brokers (Zerodha, Upstox) placing orders through the API:

| Egress posture | Decision | Why |
|---|---|---|
| `STATIC_DEDICATED` | pass | An address registered with the broker and used by one client. |
| `STATIC_SHARED` | `REVIEW_REQUIRED` | Static but shared. Zerodha permits sharing a registered IP only with immediate family; wider sharing risks suspension of the API key. |
| `DYNAMIC` | `BLOCKED` | The broker rejects order requests from unregistered addresses. This is an operational outage, not a paperwork issue. |
| `UNKNOWN` | `REVIEW_REQUIRED` | Unresolved is not approved. |

Carve-out: with `places_orders=False` the requirement does not attach — market
data, WebSocket, order book and position endpoints stay reachable from any
address. Do not gate a research backfill on an order-path control.

Operational consequences worth planning before deployment:

- Serverless (Lambda, Cloud Run) and autoscaled instances rotate their egress
  address unless placed behind a NAT gateway or a reserved static address.
  Failures surface on the first scale event, not at deploy time.
- Both Zerodha and Upstox allow one static-IP change per calendar week, with a
  primary and a secondary slot. Any blue/green or region migration that moves
  the egress address is therefore a rate-limited, scheduled operation — reserve
  the secondary slot before the cutover.

## 3. Assess residency by deployer role

- `CLIENT` — trading its own account through the broker. No in-force mandate
  requires client-side hosting in the broker's jurisdiction: SEBI CSCRF PR.DS.S2
  is in abeyance, the RBI circular is scoped to payment system data, GDPR
  regulates transfers rather than location, and SEC Rule 17a-4 sets retention
  duties rather than a hosting region. The engine records this as an advisory.
- `REGULATED_ENTITY` / `RE_VENDOR` — the analysis is real but document-dependent:
  outsourcing terms, DORA Art. 30(2)(b) contractual location clauses and Art. 29
  concentration-risk assessment, recordkeeping-location duties (SEC Rule 17a-7
  for non-resident registered broker-dealers, with its written-undertaking
  exception), and regulator notification. The engine returns `REVIEW_REQUIRED`
  and names the jurisdiction; it does not attempt a verdict it cannot support.

## 4. Keep latency out of the verdict

A region outside the broker's latency-preferred set (AWS Mumbai / GCP Mumbai for
Indian brokers) produces an `ADVISORY` finding only. It is a real engineering
consideration — round-trip time to the exchange gateway — and never a compliance
violation.

## 5. Gate and record

- `assert_deployable()` raises `BrokerDeploymentConstraintError` unless the
  status is `COMPLIANT`; every other status carries `is_deployable=False`.
- Each decision is appended to `engine.audit_trail` as a frozen record, with
  every finding's severity and citation, so a `BLOCKED` decision cannot be
  edited into an approval after the fact.
- Re-run the gate on configuration change, not only at first deploy: a NAT
  gateway replacement, a region migration, or a switch to a new API key all
  change the answer.
