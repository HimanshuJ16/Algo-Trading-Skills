# Pre-Flight Checklist — SEC Rule 15c3-5 Market Access Controls

## Scope and applicability
- [ ] Is the entity operating this gate the **broker-dealer with market access**? The rule
      binds the broker-dealer, not its non-broker-dealer customers.
- [ ] If any (c)(2) control has been allocated to a customer, is that customer itself a
      **registered broker-dealer**, is there a **written contract**, and was a **thorough
      due diligence review** performed (d)(1)? Is it recorded that the allocation relieves
      the firm of nothing (d)(2)?
- [ ] Is the order flow in scope — trading on an exchange or ATS (a)(1)? Has the
      paragraph (b) Rule 611 routing carve-out been read against the actual routing
      arrangement, rather than assumed either way?
- [ ] Are the controls under the firm's **direct and exclusive control** (d) — including
      who can change the limits, and whether any path reaches the venue around this gate?

## Limits are calibrated, not defaults
- [ ] Has **every** shipped default been replaced (5% collar, $250k notional, 5,000
      shares, 100 msgs/sec, $1m/$10m credit caps)? None of them is a regulatory figure —
      Rule 15c3-5 prescribes no numeric price, size, credit or capital parameters.
- [ ] Is the calibration **documented** against the firm's business model, capital,
      clearing arrangements and customer base? FINRA's 2026 report cites both unreasonable
      thresholds and inadequate documentation of their reasonableness as findings.
- [ ] Is every cap finite and positive, so a missing limit raises rather than reading as
      "unlimited"?
- [ ] Is `account_credit_cap_usd <= firm_credit_cap_usd`?
- [ ] For a mixed universe, is the collar differentiated by instrument rather than one
      firm-wide percentage? (LULD bands an S&P 500 name at 5% and a $2.00 stock at 20%.)

## Both limbs of every clause
- [ ] (c)(1)(i) — is the credit/capital threshold enforced **in the aggregate for each
      customer *and* the broker or dealer**, with `accumulated_firm_credit_used_usd` fed
      from real firm-wide exposure rather than left at `0.0`?
- [ ] Is the credit projection based on **orders entered**, including working orders, not
      on post-execution fills?
- [ ] (c)(1)(ii) — beyond per-order price and size, is the **"over a short period of
      time"** limb (message burst) implemented, and the **duplicative orders** limb?
- [ ] Are cumulative counters **shared across processes** if the limit is firm-wide? Ten
      gateways at 100/sec admit 1,000/sec.

## Fail-closed behaviour verified
- [ ] NaN, infinite, zero and negative `quantity` → rejected, not allowed.
- [ ] NaN, infinite, zero and negative `price` → rejected.
- [ ] NaN, infinite or negative accumulated credit on **either** limb → rejected.
- [ ] Blank or non-string `order_id` / `account_id` / `symbol` → rejected.
- [ ] `side` outside `{BUY, SELL, SELL_SHORT}` → rejected, never routed down a default
      branch. (`"SHORT"`, `"sell short"`, `"SS"`, `"BUYY"`, `""`, `None`.)
- [ ] `nbbo_mid_price` absent, zero, negative, NaN or non-numeric → **blocks** with
      `REFERENCE_PRICE_UNAVAILABLE`, and does not mask a simultaneous size breach.
- [ ] Is the fallback reference price decided and fed in explicitly, given that a
      reference-data outage now stops trading?

## Boundaries pinned
- [ ] Is an order at *exactly* each cap **allowed**, and one increment past it rejected by
      exactly one rule? (`>` versus `>=` is invisible to a suite of 500-and-2000.)
- [ ] Is the collar compared by multiplication (`abs(p - m) > collar * m`)? The division
      form rejects an order at exactly the collar for a subset of reference prices — mid
      $402.69, price $422.8245.

## Regulation SHO
- [ ] Do short sale orders require a **non-blank** locate id (whitespace is not a locate)?
- [ ] Does `short_locate_id` resolve to the stock loan system's record, so the
      documentation requirement in 203(b)(1)(iii) is actually satisfied?
- [ ] Is the 203(b)(2)(iii) bona-fide market making exception **off** unless the firm has
      taken a documented position, and is every use of it logged?
- [ ] Is Rule 200(g) order marking enforced **upstream**? A short mis-marked as a long sale
      never reaches this gate's locate check.

## Restricted list
- [ ] Is the list normalised at **both** ends (case and whitespace)? A lower-case list
      against upper-case symbols is silently inert and looks identical to an empty list.
- [ ] Is the list refreshed from its source of truth, and is a stale or unavailable list a
      **block**, not a pass?

## Change control and audit
- [ ] Is the limit set immutable at runtime, with every change going through an
      attributed path (`replace_limits(limits, authorised_by, reason)`)?
- [ ] Is prior approval obtained for intra-day threshold changes, and does every temporary
      adjustment have a scheduled reversion? (Both are named FINRA findings.)
- [ ] Is the **full** `MarketAccessCheckResult` persisted to durable storage per order —
      violations, reasons, notional, latency — and is a write failure alarmed?
- [ ] Are the supervisory procedures and the written description of the controls preserved
      as books and records per § 240.17a-4(e)(7), as (b) requires?
- [ ] Where a blocked order can be released, is there a separate supervisory review that
      the release rationale was appropriate?
- [ ] Are **all** orders in scope, with no order type excluded from the erroneous-order
      controls — market maker quotes included?

## Outside this gate, but required by the rule
- [ ] (c)(2)(i) trading halts, special order types, odd lots, Regulation NMS — covered
      elsewhere in the order path?
- [ ] (c)(2)(iii) access restricted to pre-approved persons and accounts?
- [ ] (c)(2)(iv) immediate post-trade execution reports reaching surveillance?
- [ ] (e)(1) at-least-annual effectiveness review, conducted under **written procedures**,
      **documented** and preserved?
- [ ] (e)(2) annual CEO (or equivalent) certification, preserved per § 240.17a-4(b)?
- [ ] Kill switch / mass cancel tested separately
      (`kill-switch-and-drawdown-circuit-breakers`)?
