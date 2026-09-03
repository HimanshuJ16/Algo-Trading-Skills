# Workflows for Algorithmic Trading Firm Licensing Thresholds

## Compliance screening pipeline

1. **Metric aggregation.** From the data warehouse, over a documented window:

   | Input | How to compute it |
   |---|---|
   | `off_exchange_volume_usd` | Total executed otherwise than on a national securities exchange of which the firm is a member. Classify by execution venue, not by routing intent. |
   | `exempt_off_exchange_volume_usd` | Only volume evidenced as Rule 15b9-1(c)(1) exchange-routed Rule 611 / Options OPP flow, or (c)(2) stock-leg-of-a-stock-option-order flow. If it is not evidenced, it is not exempt — leave it at 0.0. |
   | `peak_orders_per_second` | Highest order count in any single **calendar clock second**, per exchange. NSE specifies the clock second for TOPS; a rolling window will not reconcile with the exchange's own count. |
   | `avg_messages_per_second_per_instrument` | Article 19(1)(a): per liquid instrument, over that instrument's relevant trading hours; take the maximum across instruments. Exclude DEA-client and client-order messages. |
   | `avg_messages_per_second_all_instruments` | Article 19(1)(b): sum the per-instrument indicators across the venue. |

   The aggregation layer is responsible for finite, non-negative, well-typed
   values. Dataclass validation is a backstop, not the first line of defence.
   Leave an Article 19 average as `None` when it was not computed — never
   substitute `0.0`.

2. **Threshold configuration.** Construct `LicensingThresholdEvaluator` with
   defaults first. Override only to screen more strictly, and record the
   override in the policy registry with its approval. A `0` override is
   honoured, not treated as "unset".

3. **Evaluation.** Pass the snapshot to `evaluate()`. Every check runs. The
   result is a frozen `LicensingComplianceReport` carrying `requires_registration`,
   `manual_review_required`, `violations`, `manual_review_items`, `rule_id`,
   `evaluated_at` (UTC) and `schema_version`.

4. **Routing, by outcome.**

   | Outcome | Route | Action |
   |---|---|---|
   | `requires_registration` | CCO **and** General Counsel, immediately | Throttle order rates, disable the offending off-exchange routing, or stop trading, until the registration position is resolved. |
   | `manual_review_required` only | Counsel, same business day | Supply the missing measurement, or obtain an opinion on the out-of-scope regime. Do **not** halt the desk on this alone, and do **not** file it as compliant. |
   | `is_clear` | Compliance log | Archive. Still not a legal opinion. |

5. **Remediation.** Remediation of one trigger does not waive another:
   throttling order rates does nothing for an off-exchange routing breach.
   Re-run after each remediation and retain both reports.

6. **Audit and retention.** Persist every report immutably alongside the input
   snapshot. Reports are inputs to regulatory examination; never overwrite a
   historic one.

## Failure and recovery boundaries

- **Dataclass validation failure** (non-finite or negative metric, `bool` in a
  numeric field, exempt volume exceeding the total, unsupported jurisdiction):
  quarantine the upstream aggregation job. Do not run the evaluator until the
  source produces well-formed metrics.
- **Exempt off-exchange volume claimed.** The engine raises a review item
  every time, because Rule 15b9-1(c)(2) requires written policies and
  procedures preserved three years consistent with Rule 17a-4. Confirm the
  evidence exists before the exemption is relied on; an unevidenced claim
  converts a condition (c) breach into a false clean report.
- **EU averages missing.** A high peak with no averages is undetermined. The
  fix is to compute the Article 19 averages, not to raise the threshold. ESMA
  expects self-assessment at least monthly; a firm may also request its
  venue's estimate of average messages per second within two weeks of month
  end, while remaining responsible for its accuracy.
- **Indian member flow.** The `IN` branch declines to conclude for
  non-retail-API flow. Resolve it through counsel and the exchange's
  algo-approval process, not by setting `is_retail_api_algo_flow=True` to
  clear the report.
- **Multi-jurisdiction firms.** Run one evaluation per jurisdiction and retain
  each independently. A "primary jurisdiction" claim must be confirmed with
  counsel; it is not an input this module accepts.
- **Customer accounts.** Any single `True` escalates, whatever else the
  metrics show. It defeats Rule 15b9-1(b) and the MiFID II Article 2(1)(d)
  own-account exemption simultaneously.
- **Threshold drift.** When a published figure changes, update the override
  and re-run. `evaluated_at` and `schema_version` make the backfill
  deterministic against the rule version that produced each prior report.
- **Unrecognised jurisdiction.** Fails closed to a manual review item with
  `rule_id = None`. Never treat as compliant.
- **Log injection.** Treat upstream-captured strings as untrusted. Log
  `report` fields with `%s` placeholders; never f-string raw fields into a log
  line.
