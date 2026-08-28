---
name: sec-rule-15c3-5-risk-controls-us
description: >-
  Fail-closed pre-order-entry market access gate under SEC Rule 15c3-5 (17 CFR 240.15c3-5) — account and firm credit thresholds, single-order quantity/notional caps, a firm-calibrated price collar against an NBBO reference, message-burst and duplicate-order controls, Regulation SHO locate verification, and restricted-list blocking, with every control mapped to the clause that requires it.
domain: Compliance & Market Governance
subdomain: SEC Rule 15c3-5 Market Access Controls
tags: ["sec-rule-15c3-5", "market-access-rule", "pre-trade-risk", "credit-thresholds", "fat-finger-collar", "reg-sho-locate", "duplicative-orders", "fail-closed"]
brokers_frameworks: ["SEC Rule 15c3-5 (17 CFR 240.15c3-5)", "SEC Release No. 34-63241 (adopting release)", "Regulation SHO Rule 203(b) (17 CFR 242.203(b))", "FINRA 2026 Annual Regulatory Oversight Report — Market Access Rule", "LULD Plan (NMS plan to address extraordinary market volatility)", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when building or auditing the pre-order-entry gate that a broker-dealer with market access must place in front of every order it or its customers send to an exchange or ATS. 17 CFR 240.15c3-5(b) obliges "a broker or dealer with market access, or that provides a customer or any other person with access to an exchange or alternative trading system through use of its market participant identifier or otherwise" to establish, document and maintain risk management controls; paragraph (d) puts those controls "under the direct and exclusive control of the broker or dealer", which is what eliminates the practice the adopting release calls "unfiltered" or "naked" access.

The engine maps each check onto the clause that requires it, so an auditor can trace a rejection to a rule: **(c)(1)(i)** credit and capital thresholds "in the aggregate for each customer **and** the broker or dealer"; **(c)(1)(ii)** erroneous orders "that exceed appropriate price or size parameters, on an order-by-order basis **or over a short period of time**, or that indicate duplicative orders"; **(c)(2)(i)** regulatory requirements "that must be satisfied on a pre-order entry basis" (the adopting release names exchange rules on special order types, trading halts and odd lots, and SEC rules under Regulation SHO and Regulation NMS); **(c)(2)(ii)** securities the person "is restricted from trading".

## When NOT to Use

- **When you are not the broker-dealer.** Rule 15c3-5 binds the broker-dealer with market access. A proprietary trading firm or fund reaching the market through a broker is not the addressee, and building this gate does not discharge the broker's obligation. Paragraph (d)(1) permits allocating control over *specific (c)(2) regulatory* controls by written contract, after a thorough due diligence review, **only to a customer that is itself a registered broker or dealer** — and (d)(2) states that allocation "shall not relieve" the broker-dealer of any obligation under the section. There is no route by which a non-broker-dealer customer takes over these controls.
- **Outside the US, or off-exchange/off-ATS.** "Market access" is defined in (a)(1) by exchange or ATS membership or subscription. For EU venues use `mifid-ii-algo-trading-compliance-eu` (RTS 6 Art. 15); for the UK, `uk-fca-algorithmic-trading-systems-controls`. Do not port a 15c3-5 control set to another jurisdiction on the assumption the obligations coincide.
- **As the firm's whole 15c3-5 programme.** This is the pre-order-entry gate only. It does not restrict system access to authorised persons ((c)(2)(iii)), route immediate post-trade execution reports to surveillance ((c)(2)(iv)), perform the annual effectiveness review under written procedures ((e)(1)), or produce the CEO certification ((e)(2)). It also covers only two of the pre-order-entry regulatory requirements in (c)(2)(i) — Regulation SHO locates and the restricted list — not trading halts, special order types, odd lots or Regulation NMS.
- **As a source of numeric limits.** The rule prescribes **none**. Every default in `SecRule15c35Limits` is an engineering placeholder. See the Prerequisites note.
- **As a firm-wide cumulative control across processes.** The burst and duplicate windows live in one process. Ten gateway processes each admitting 100 messages/second admit 1,000. Cumulative limits belong in shared state.
- **For order flow inside the paragraph (b) routing carve-out**, where a broker-dealer routing on behalf of an exchange or ATS to reach protected quotations under Rule 611 is excepted from the rule "except with regard to paragraph (c)(1)(ii)". Read (b) against your actual routing arrangement rather than assuming the whole rule applies or that none of it does.

## Prerequisites

- Order payload (`MarketAccessOrder`): `order_id`, `account_id`, `symbol`, `side` (one of `BUY`, `SELL`, `SELL_SHORT` — nothing else is accepted), `quantity`, `price`, `nbbo_mid_price`, `accumulated_credit_used_usd`, `accumulated_firm_credit_used_usd`, `short_locate_id`, optional `timestamp_sec`, optional `is_bona_fide_market_making`.
- Limits (`SecRule15c35Limits`): `firm_credit_cap_usd`, `account_credit_cap_usd`, `max_single_order_notional_usd`, `max_single_order_qty`, `max_price_collar_pct`, `max_order_rate_per_sec`, `burst_window_sec`, `duplicate_window_sec`, `restricted_symbols`, `allow_market_maker_locate_exception`. Every cap must be finite and positive: a zero or absent limit raises rather than meaning "unlimited".
- **Firm-calibrated values.** Rule 15c3-5 sets no numeric price or size parameters; the adopting release leaves them to the broker-dealer, and FINRA's 2026 Annual Regulatory Oversight Report reports firms setting them "at unreasonable thresholds based on a firm's business model" and failing to document why they are reasonable. The shipped defaults (5% collar, $250k notional, 5,000 shares, 100 msgs/sec) are placeholders that would themselves be a finding if left in place. Record the calibration alongside the limits — that documentation is what the (e)(1) annual review consumes.
- **Both exposure figures.** (c)(1)(i) is aggregate "for each customer **and** the broker or dealer". A gate fed only `accumulated_credit_used_usd` enforces half the clause; the firm limb needs `accumulated_firm_credit_used_usd` from wherever firm-wide exposure is aggregated.
- A reference price you are willing to block on. The collar is evaluated against `nbbo_mid_price`; when it is absent or unusable the order is **rejected**, so a flaky reference feed becomes a trading outage rather than a silent control bypass. Decide that trade-off before deployment.
- One monotonic clock domain. If you supply `timestamp_sec`, every order must carry a timestamp from the same monotonic source, in non-decreasing order; otherwise leave it `None` and let the engine clock the message.
- Threshold convention, applied uniformly: **the configured limit is itself permitted; a breach requires exceeding it.** An order of exactly `max_single_order_qty` passes.

## Workflow

1. **Structural validation — fail closed before any limit comparison.** Reject the order outright (`INVALID_ORDER`) if any field a limit is compared against is unusable, and evaluate nothing further.
   - **Decision point — a malformed order is a rejection, not an exception and not a pass.** Every comparison against NaN is `False`, so an unvalidated NaN quantity breaches no cap and is *allowed*. A negative quantity is worse: `quantity * price` goes negative and slides under every positive notional cap, so a $150m sell prices out at −$150m and passes. Require a finite quantity and price strictly greater than zero.
   - **Decision point — whitelist the side; never default the unknown case.** `side.upper() == "SELL_SHORT"` routes `"SHORT"`, `"sell short"` and `"SS"` down the non-short branch, which skips the Regulation SHO locate check entirely — a naked short through a gate that reports itself green. Accept only `BUY`, `SELL`, `SELL_SHORT`; reject everything else.
2. **Single-order size parameters — (c)(1)(ii).** Compare `quantity` against `max_single_order_qty` and `quantity * price` against `max_single_order_notional_usd`. Both fire independently; report both.
3. **Credit and capital thresholds — (c)(1)(i).** Project `accumulated_credit_used_usd + notional` against the account cap and `accumulated_firm_credit_used_usd + notional` against the firm cap.
   - **Decision point — the projection must be pre-trade, not post-execution.** The release requires the broker-dealer to "assess compliance with the applicable threshold on the basis of exposure from orders entered on an exchange or ATS, rather than relying on a post-execution, after-the-fact determination". Committed exposure means orders *entered*, including working orders, not filled quantity.
4. **Price collar — (c)(1)(ii), price parameter.** Reject when `abs(price − mid) > collar × mid`.
   - **Decision point — an unusable reference price blocks the order; it does not skip the check.** `if mid > 0:` disables the fat-finger control at exactly the moment a stale or absent feed makes a fat finger likeliest. The engine emits `REFERENCE_PRICE_UNAVAILABLE`, and does so *without* masking a simultaneous size breach.
   - **Decision point — compare by multiplication, not division.** `abs(price − mid) / mid > collar` spuriously rejects an order priced at exactly the collar for a subset of reference prices: mid $402.69 with price $422.8245 divides to `0.05000000000000001` and is rejected at a 5% collar it exactly meets.
5. **Regulation SHO locate — (c)(2)(i).** For a `SELL_SHORT`, require a non-empty `short_locate_id`. 17 CFR 242.203(b)(1) prohibits accepting a short sale order unless the broker-dealer has borrowed the security, entered a bona-fide arrangement to borrow it, or has reasonable grounds to believe it can be borrowed for delivery when due — **and** has documented compliance.
   - **Decision point — whitespace is not a locate.** `"   "` is truthy in Python, so a blank locate field passes a naive check. Strip before testing.
   - **Decision point — the 203(b)(2)(iii) bona-fide market making exception is a firm determination, not an order attribute.** The engine honours it only when the order asserts it *and* the firm has set `allow_market_maker_locate_exception`, and it logs every use at `WARNING`. Default closed.
6. **Restricted securities — (c)(2)(ii).** Match the normalised symbol against the normalised restricted set. Normalise at *both* ends: a list configured in lower case never matches an upper-case incoming symbol, and the control is then silently inert.
7. **Cumulative controls — (c)(1)(ii), "over a short period of time, or that indicate duplicative orders".** Count messages per account in a rolling window against `max_order_rate_per_sec`, and fingerprint recent orders on (account, symbol, side, quantity, price).
   - **Decision point — a rejected order still consumed a message, but it did not reach the venue.** So it counts toward the burst budget and does *not* seed the duplicate window: a corrected resubmission after a rejection is not a duplicate of anything.
8. **Persist the decision and control the limits.** Write the full `MarketAccessCheckResult` — `order_id`, `is_allowed`, `triggered_violations`, `rejection_reasons`, `notional_usd`, `audit_notes` — to durable storage. `SecRule15c35Limits` is frozen; change limits only through `replace_limits(limits, authorised_by, reason)`, which logs the change.
   - **Decision point — an intraday limit increase is a control change, not a configuration tweak.** FINRA's 2026 report cites inadequate oversight of intraday changes to credit and capital thresholds, including obtaining approval before adjusting them, and temporary adjustments that never revert. Attribute the change and schedule its reversal at the time you make it.

> Full procedure: see `references/workflows.md`.
> Clause-by-clause regulatory map with source links: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Presenting "5% from NBBO mid" as a regulatory requirement.** Rule 15c3-5 prescribes no numeric price or size parameters anywhere in its text; (c)(1)(ii) says only "appropriate price or size parameters". Hard-coding 5% and citing the rule for it is a documentation defect that survives into the (e)(1) annual review and the (e)(2) CEO certification. A uniform percentage is also wrong on its own terms across a mixed universe — the LULD Plan itself bands a $2.00 stock at 20% and an S&P 500 name at 5%.
- **Fail-open on malformed data.** NaN quantity, NaN price, a negative quantity, a NaN accumulated-credit figure: each defeats every comparison it participates in and the order is allowed. This is the single highest-value class of test for a market access gate, and it never occurs in a backtest.
- **Skipping the collar when the reference price is missing.** Guarding the collar with `if mid > 0:` turns a data outage into an open gate. Block instead.
- **Keying the locate check off an unvalidated side string.** Anything outside the whitelist bypasses Regulation SHO. Related: Rule 200(g) requires every sell order to be marked long, short or short exempt, so a short mis-marked `SELL` upstream never reaches the locate check at all — the marking control is upstream of this gate, not inside it.
- **Citing FINRA Rule 4320 as the locate rule.** It is not. Rule 4320 is a *delivery* rule for non-reporting threshold securities: once a fail-to-deliver position persists 13 consecutive settlement days it must be closed out, and until then the participant may not accept a short sale order without borrowing or arranging to borrow — a pre-borrow, stricter than a locate, and scoped to those securities. The general locate obligation is Regulation SHO Rule 203(b)(1); close-out for reporting securities is Rule 204.
- **Enforcing the customer credit limb and calling (c)(1)(i) done.** The clause says "in the aggregate for each customer **and** the broker or dealer". A per-account cap that every account respects still permits the firm to exceed its own capital threshold in aggregate.
- **Implementing only the order-by-order limb of (c)(1)(ii).** The clause covers erroneous orders "on an order-by-order basis **or over a short period of time**, or that indicate duplicative orders". A thousand individually compliant orders in a second is precisely the runaway-algorithm case the rule addresses, and a duplicate-order control is named explicitly.
- **Counting the burst window per process.** Per-process counters do not enforce a firm limit across a gateway fleet. If the limit is firm-wide, the state must be too.
- **Excluding order types from the erroneous-order controls.** FINRA's 2026 report lists "excluding certain orders from a firm's pre-trade erroneous controls based on order types" as a finding. The SEC's FAQ is likewise explicit that the rule applies to all orders including market maker quotes, with no exclusion for them.
- **Mutating limits in place during the session.** An in-place change leaves no record of who widened what, or of the temporary adjustment that was never reverted.
- **Treating a soft block as a control.** Where an order is released after a control fires, FINRA's effective practice is a separate supervisory review that the release rationale was appropriate. An override path with no second pair of eyes is the control's absence with extra steps.
- **Case-sensitive restricted lists.** A list keyed one way and symbols arriving the other silently disables (c)(2)(ii) — the failure looks identical to an empty list.
- **Assuming a venue-side band substitutes for the firm control, or vice versa.** LULD price bands are applied by the venue and pause trading; the 15c3-5 collar is applied by the broker-dealer before the order is routed. Neither discharges the other.

## Verification

- Valid order inside every limit $\implies$ `is_allowed=True`, no violations, and `notional_usd` equal to `quantity × price`.
- **Boundary checks (must be allowed):** quantity exactly `max_single_order_qty`; notional exactly `max_single_order_notional_usd` (500 × $200 against a $100,000 cap); committed credit + notional exactly at the account cap; and price $422.8245 against mid $402.69 at a 5% collar, which the division form rejects. One increment past each must be rejected by exactly one rule.
- **Fail-closed checks (must reject with `INVALID_ORDER`):** NaN, infinite, zero and negative quantity; NaN, infinite, zero and negative price; NaN, infinite and negative accumulated credit on either limb; blank or non-string `order_id`, `account_id`, `symbol`; `side` of `"SHORT"`, `"sell short"`, `"SELL-SHORT"`, `"SS"`, `"BUYY"`, `""` and `None`. An `INVALID_ORDER` result must carry that code alone and a `notional_usd` of `0.0`.
- **Reference price:** `nbbo_mid_price` of `0.0`, negative, NaN, infinite, a string, or absent $\implies$ `REFERENCE_PRICE_UNAVAILABLE`, and it must not mask a simultaneous `SINGLE_ORDER_QTY_CAP` breach.
- **Regulation SHO:** `SELL_SHORT` with `short_locate_id` of `None`, `""`, `"   "` or a non-string $\implies$ `SHORT_SALE_LOCATE_MISSING`; with a real locate id $\implies$ allowed. `is_bona_fide_market_making=True` alone must still reject; it may pass only when the firm has enabled `allow_market_maker_locate_exception`, and that pass must emit a `WARNING`.
- **Credit limbs:** an account well inside its own cap, against a firm figure at the cap, $\implies$ `FIRM_CREDIT_CAP_EXCEEDED` and nothing else.
- **Restricted list:** a list configured in lower case must still block an upper-case incoming symbol, and vice versa. A bare string passed as `restricted_symbols` must raise, not iterate into single characters.
- **Cumulative controls:** with `max_order_rate_per_sec=3`, the fourth message inside the window $\implies$ `RAPID_ORDER_BURST`; the counter is per account, rolls forward as the window ages, and counts rejected orders. An identical resubmission inside `duplicate_window_sec` $\implies$ `DUPLICATE_ORDER_DETECTED`; a resubmission after a *rejection* must not. Window state must not grow without bound across 500 orders.
- **Mis-configuration must raise:** any cap zero, negative, NaN, infinite or non-numeric; `account_credit_cap_usd > firm_credit_cap_usd`; a negative or NaN collar; `max_order_rate_per_sec` of `0`, `-1`, `1.5` or `True`; a zero window; a bare string as `restricted_symbols`; a non-`SecRule15c35Limits` object passed to the engine; `replace_limits` without an authoriser and a reason.
- Run `python -m unittest discover -s skills/sec-rule-15c3-5-risk-controls-us/scripts` and confirm all tests pass.

## Related Skills

- `risk-control-unit-testing-framework`
- `risk-limit-breach-escalation-matrix`
- `us-reg-sho-short-sale-locate-requirements`
- `kill-switch-and-drawdown-circuit-breakers`
- `risk-control-bypass-audit-logging`
- `mifid-ii-algo-trading-compliance-eu`
