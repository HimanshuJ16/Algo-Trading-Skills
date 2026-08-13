# Integration Workflow: Binary Options Compliance and Risk Gate

Cited sources for every rule referenced here are in `references/standards.md`.

## Phase 0 — Decide whether the product is in scope at all

Before writing strategy code, answer three questions in order. Two of them can end the
project, which is the point of doing them first.

1. **Is the product a binary option?** Assess function, not name. Digital options,
   fixed-return options, one-touch / no-touch, and binary-payout event contracts all have
   the same discontinuous payoff. ESMA's 3 July 2026 statement confirms binary-payout
   event contracts that are financial instruments sit inside the existing binary options
   measures — "prediction market" branding does not move a product out of scope.
2. **Is it lawful for this client base?** For retail clients in the UK, Australia, and EU
   member states, the answer is generally no. For individuals in Canada with sub-30-day
   maturities the answer is no regardless of accreditation.
3. **Is your entity authorised, and is the venue registered?** These are separate
   questions from (2). ESMA notes that distributing in-scope event contracts in the EU
   requires investment firm authorisation even when distributing only to non-retail
   clients.

If (2) or (3) fails, stop. No amount of alpha makes a prohibited trade placeable.

## Phase 1 — Establish the client facts

Two distinct attributes are required, and conflating them is a documented fail-open:

| Attribute | Field | Used by |
|---|---|---|
| MiFID/FCA categorisation | `client_type` → `client_category` | UK, EU, AU retail prohibitions |
| Natural person or entity | `is_natural_person` | Canada MI 91-102 |

Canada's prohibition applies to individuals **including accredited investors**, so a
client who is `PROFESSIONAL` *and* a natural person is still prohibited from sub-30-day
binaries. If `is_natural_person` is unknown, the rule denies rather than guessing.

`client_type` accepts a `ClientCategory` or a case-insensitive string. It does **not**
accept unrecognised values — `"CONSUMER"` raises `ValueError` rather than falling through
to a permissive branch.

## Phase 2 — Establish the venue

`venue_status` defaults to `VenueStatus.UNKNOWN`, and `UNKNOWN` is denied.

- Maintain the registered-venue list as **dated configuration with a named owner**, not as
  literals in the compliance module. The joint CFTC/SEC alert's named DCM list has already
  moved since publication.
- In the US, verify against the CFTC's current DCM list before trading, and check the CFTC
  Registration Deficient (RED) List for unregistered foreign entities.
- Re-verify on a schedule. A venue that was registered when you wrote the config is not
  necessarily registered today.

## Phase 3 — Wire the gate

```python
import logging
from datetime import datetime, timedelta, timezone

from binary_options import (
    BinaryOptionsManager, ClientCategory, ComplianceEngine, Jurisdiction,
    RiskEngine, TradeContext, VenueStatus,
)

logging.basicConfig(level=logging.INFO)

compliance = ComplianceEngine(
    # Date you last checked the rule table against primary regulator sources.
    ruleset_last_verified=datetime(2026, 8, 13, tzinfo=timezone.utc),
    ruleset_max_age=timedelta(days=180),
)
risk = RiskEngine(
    max_notional_per_trade=250_000.0,
    max_aggregate_notional=5_000_000.0,
    max_pin_risk_exposure=1_000_000.0,
    pin_window=timedelta(hours=24),
)
manager = BinaryOptionsManager(compliance, risk)

context = TradeContext(
    asset_id="ORD-00123",                       # idempotency key for the risk book
    underlying="ES",
    notional=100_000.0,
    strike=5_000.0,
    expiry=datetime(2026, 10, 1, 20, 0, tzinfo=timezone.utc),   # must be tz-aware
    jurisdiction=Jurisdiction.US_CFTC,
    client_type=ClientCategory.PROFESSIONAL,
    venue="DCM-FROM-DATED-REGISTRY",
    venue_status=VenueStatus.REGISTERED,        # from your dated registry, not a guess
    is_natural_person=False,
)

decision = manager.process_order(context)
if decision["status"] != "APPROVED":
    # Persist the whole record: reason_code and citation are the audit evidence.
    raise RuntimeError(decision["reason_code"])
```

Order of evaluation is deliberate: **compliance first, then risk**. A prohibited trade
must be reported as a regulatory rejection even when it would also have breached a risk
limit, because the two have different escalation paths — one goes to compliance and
potentially to a regulator, the other to the risk desk.

## Phase 4 — Exposure accounting

- `process_order` books approved exposure via `register_trade`, keyed by `asset_id`.
  Re-submitting the same id **replaces** its exposure, so a retry after an ambiguous
  response cannot double-count. Use a stable per-order id, not a per-attempt one.
- Pass `register=False` to evaluate an order without touching aggregate limits — use this
  for what-if checks, and never for the live path.
- Call `release_trade(asset_id)` on settlement, expiry, or cancellation. Trades whose
  expiry has passed are excluded from exposure automatically, but stale entries still
  accumulate in memory until released.
- `current_exposure(now)` returns `aggregate_notional` and `near_expiry_notional` for
  monitoring.
- `RiskEngine` is guarded by a lock. An unsynchronised read-then-register lets two
  concurrent orders each pass a limit they jointly breach.

## Phase 5 — Audit trail

Retain, per decision: `asset_id`, `status`, `reason_code`, `citation`, `message`, the
evaluated `now`, and the input context. Rejections are the evidence that the gate was
operating — a compliance review that finds only approvals cannot distinguish a working
gate from a disabled one.

Every decision is also emitted through the `binary_options` logger: approvals at INFO,
rejections at WARNING. Route this into your retention pipeline; see
`record-retention-periods-by-jurisdiction` for how long to keep it.

## Phase 6 — Re-verification cadence

The rule table is dated, and it will go stale. Verified precedents for exactly that:
ESMA's measure lapsed in 2019; ASIC's was extended in 2022 to 2031; ESMA issued the event
contracts statement in July 2026.

1. Assign a named owner and a review cadence at or inside `ruleset_max_age`.
2. On review, re-check each source in `references/standards.md` against the regulator's
   current publication — not against a secondary summary.
3. Update `ruleset_last_verified` **only** when the check has actually been performed.
   Bumping the date to silence the warning without doing the work removes the one control
   that catches a stale ruleset.
4. Record what changed. If a rule's shape changes (not just a threshold), the predicate
   itself needs rewriting — Canada's maturity-and-individual test is the worked example of
   a rule that a retail/professional switch cannot express.

See `regulatory-change-monitoring-service-integration` for automating step 2's alerting.
