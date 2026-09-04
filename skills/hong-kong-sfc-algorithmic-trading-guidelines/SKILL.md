---
name: hong-kong-sfc-algorithmic-trading-guidelines
description: >-
  Use when an algorithmic system or DMA gateway sends orders to the Stock Exchange of
  Hong Kong and must satisfy SFC Code of Conduct paragraph 18 and Schedule 7 electronic
  trading requirements. The SFC sets no numeric limits; you calibrate them.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: regulatory-compliance-global
  tags: sfc, schedule-7, hong-kong-regulation, algorithmic-trading, pre-trade-controls, covered-short-selling, tick-rule, kill-switch
  brokers_frameworks: "SFC Code of Conduct paragraph 18 and Schedule 7; SFC Circular on Algorithmic Trading (13 Dec 2016); Securities and Futures Ordinance ss.170-172; SEHK Rules of the Exchange Rule 563D; SEHK Eleventh Schedule Short Selling Regulations; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a licensed or registered person (an SFC-licensed corporation or a
registered institution) runs an algorithmic trading system, an internet trading facility or
a DMA gateway that sends orders to the **Stock Exchange of Hong Kong (SEHK)**, and you are
building or reviewing the pre-trade layer that sits between the strategy and the exchange
session.

The Hong Kong obligations live in three places, and conflating them is the most common
error in this area:

| Layer | Instrument | What it governs |
|---|---|---|
| Conduct | **Code of Conduct paragraph 18 and Schedule 7** | Governance, qualification, testing, automated pre-trade controls, the kill switch, and record keeping. Written as *outcomes* — it prescribes no numbers. |
| Statute | **SFO sections 170, 171, 172** | Naked short selling is a criminal offence; a short selling order must be identified and covered by documentary assurance; the order must be marked "short" on input. |
| Exchange rules | **Rules of the Exchange Rule 563D and the Eleventh Schedule** | Short selling limited to Designated Securities and to permitted sessions/order types; the tick rule. |

## When NOT to Use

- **As the source of your pre-trade limits.** Neither the SFC nor SEHK sets a maximum order
  value or a price band for a licensed corporation. The defaults in the reference
  implementation (HKD 10,000,000, 5%) are placeholders. The SFC's first risk-management
  finding in its 13 December 2016 circular was inadequate analysis and documentation behind
  threshold parameter values — copying a number out of this skill reproduces exactly that
  deficiency.
- **As the Exchange's own order-price validation.** SEHK polices order prices itself
  (Rule 505A's 9-times-nominal-price bar; Rules 506A/507A's "twenty-four spreads or 5%"
  bands measured from the *current bid/ask*, not the last trade; the ±15% POS and ±5% CAS
  order-input bands). A firm price band is an erroneous-order control that sits *in front
  of* those rules, and satisfying one says nothing about the other.
- **Outside SEHK-listed cash equities.** SFO section 170 applies only to sales "at or
  through a recognized stock market"; the short selling rules modelled here are SEHK's.
  Futures on HKFE, Northbound Stock Connect (see `shanghai-shenzhen-connect-programs`) and
  off-exchange trades follow different regimes.
- **As a substitute for post-trade surveillance.** Schedule 7 paragraphs 2.1.1(b) and 3.3.2
  require regular post-trade review for manipulative or abusive activity, and 3.3.3 requires
  immediate steps once it is identified. A pre-trade gate is not that — see
  `wash-trade-and-spoofing-self-detection`.
- **As proof of a right to sell.** `has_locate_borrow` is an assertion the caller passes in.
  The engine cannot see your stock loan book, and a "hold" the lender later withdraws is a
  different fact from a settled borrow.
- **As the system of record.** `audit_trail` is in memory. Schedule 7 paragraph 1.3.2(b)
  requires audit logs to be retained for **not less than 2 years**; section 171 documentary
  assurances have their own **12-month** retention. See `record-retention-periods-by-jurisdiction`.

## Prerequisites

- Python 3.10+ (`from __future__ import annotations`; stdlib only).
- **Firm attestations per order**: `algo_authorised_for_production` (Schedule 7 §1.1.1(b),(d)),
  `algo_testing_signed_off` (§3.2.1), `operator_approved_to_use` (§3.1.2). Hong Kong has no
  per-developer registration regime — the requirement is that the people involved are
  *suitably qualified* and that at least one responsible officer or executive officer owns
  the system (§1.1.1(a)).
- **Firm-calibrated thresholds, with documented rationale**: `max_order_value_hkd`,
  `max_price_deviation_pct`, and optionally `max_order_quantity`,
  `max_adv_participation_pct`, `max_messages_per_interval`.
- **Market data**: nominal (last traded) price, and for a short sale the session-dependent
  reference price — best current ask in CTS, the POS reference price in POS, the CAS
  reference price in CAS.
- **Reference data**: SEHK's *Designated Securities Eligible for Short Selling* list, as at
  the order's date. The list is revised periodically; a stale copy is a live compliance risk.
- **Short sale evidence**: confirmed borrow/locate, a reference to the section 171
  documentary assurance, and the short-sale marking flag.

## Workflow

1. **Kill switch first (Schedule 7 §1.2.1).** Before any other control, check whether a
   firm-, algo- or client-scoped switch is engaged.
   - **Decision point — a firm-wide-only kill switch is a finding, not a design.** The SFC's
     2016 circular criticised switches implemented only "at the exchange connectivity level
     or the algorithmic engine level ... instead of implementing them at more disaggregated
     levels (eg, relating to a particular client or algorithmic strategy)", because the firm
     then has to stop everything to stop anything. Scope by algo and by client.
   - **Decision point — blocking new orders is only half of §1.2.1.** The paragraph requires
     the ability to "(a) immediately prevent the system from generating and sending orders
     ... and (b) cancel any unexecuted orders that are in the market." This gate does (a).
     Wire (b) to the exchange session's mass-cancel; an engaged switch does not touch resting
     orders.
   - **Decision point — engaging and releasing both need a named human and a reason.** The
     circular's fourth risk-management observation was a pre-trade price limit overridden on
     verbal approval alone. Releasing a kill switch is a control override in everything but
     name.

2. **Authorisation, testing and operator qualification (§1.1, §3.1, §3.2).** Reject an
   algorithm that is not signed off for production, whose version was not tested before
   deployment, or whose submitting operator is not approved to use the system.

3. **Automated pre-trade controls (§2.1.1(a), §3.3.1).** Evaluate the firm's thresholds:
   notional value, order quantity, price deviation from the nominal price, participation
   against average daily volume, message rate, and — for a sliced order — child price and
   quantity against the parent.
   - **Decision point — compare unrounded.** Rounding a 5.004% deviation to two decimals
     produces "5.00%", which passes a 5.00% limit. Round for display, never before the test.
   - **Decision point — missing market data blocks; a malformed order raises.** A nominal
     price of `0.0` or `None` (a stock with no trade yet, or a dropped feed) means the price
     band *could not be evaluated*, which is `MISSING_MARKET_DATA` and a blocked order — not
     a deviation of 0.0% and not a `ZeroDivisionError` in the order path. An unknown session
     token or a negative quantity is a defect in the calling strategy and raises `ValueError`.
   - **Decision point — child orders get the parent's controls.** The SFC states that child
     orders should be subject to the same pre-trade and post-trade controls as parent orders,
     and that a child's limit price or aggregate quantity must not exceed the parent's.

4. **Covered short selling — four separate obligations, not one (§SFO 170–172, Rule 563D,
   Eleventh Schedule Reg (15)).** A short sale that clears the locate check can still be
   unlawful:
   - **SFO s.170** — a presently exercisable and unconditional right to vest the securities
     in the purchaser. Naked short selling is a criminal offence (max HK$100,000 and 2 years).
   - **SFO s.171** — documentary assurance that the sale is short and covered, provided no
     later than when the order is placed, obtained before transmission and retained ≥12 months.
   - **SFO s.172 / Eleventh Schedule Reg (5)(b)** — the order is marked "short" on input.
   - **Rule 563D(1)** — Designated Securities only, and in POS and CAS "only at-auction limit
     orders may be input into the System as short selling orders".
   - **Eleventh Schedule Reg (15)** — the tick rule: not below the best current ask (CTS) or
     the CAS reference price (CAS); Rule 501(G)(3)(d) applies the POS reference price in POS.
   - **Decision point — no reference price means no approval.** If the tick-rule reference
     price is absent, the control has not been satisfied; fail closed.
   - **Decision point — an exemption is a claim, not a fact.** Rule 563D(1) exempts market
     makers, liquidity providers and specified hedging/arbitrage participants from the
     Designated Securities and tick restrictions. Record the claimed category; never let it
     waive the section 170 cover check on an order-level flag.

5. **Record every decision (§1.3, §3.4.2).** Approvals as well as rejections, time-stamped
   with a unique reference, listing every violation raised — the Annex to Schedule 7 asks for
   exactly this, including "compliance validation exceptions" and "erroneous order inputs".
   Persist to a durable append-only store for ≥2 years, alongside the parameters the
   algorithm took into account for the order (§3.4.2).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Citing "Schedule 7 paragraph 4".** There is no paragraph 4. Schedule 7 runs 1 (general
  electronic trading), 2 (internet trading and DMA) and 3 (algorithmic trading). The kill
  switch is **1.2.1**, not 3 or 4; pre-trade controls for DMA are **2.1.1(a)**; algorithmic
  risk management is **3.3.1**. A wrong paragraph number in a compliance artefact is the kind
  of error an SFC inspection finds first.
- **Presenting firm thresholds as SFC requirements.** "The SFC mandates a 5% price deviation
  limit" is false. The 5% figures that *do* exist in Hong Kong belong to SEHK — the CAS
  order-input band and one leg of the "twenty-four spreads or 5%" CTS bands — and they are
  measured from different reference prices than a firm's own erroneous-order check.
- **Treating a locate as the whole short selling test.** An order can be genuinely covered
  and still breach the Ordinance (unmarked, no documentary assurance) or the Rules of the
  Exchange (not a Designated Security, below the best ask, a plain limit order in the CAS).
- **Selling short in the auction sessions with the wrong order type.** In POS and CAS only
  at-auction limit orders may be input as short selling orders. A strategy that carries its
  CTS order type into the closing auction will be rejected by SEHK — or worse, silently
  reshaped by an intermediary.
- **Working from a stale Designated Securities list.** The list is revised periodically. A
  name that was shortable last quarter may not be today, and the engine cannot detect that
  your snapshot is old.
- **Rounding before the threshold test.** `round(deviation, 2) > 5.0` approves every breach
  between 5.000% and 5.005%.
- **Letting missing market data read as a passing value.** A nominal price of zero produces
  either a crash or, if someone "fixes" it with a default, a deviation of 0.0% — the most
  compliant-looking number in the report, generated by the absence of data.
- **Recording only the first breach.** An oversized naked short is not "an order value
  breach"; filing it as one loses the criminal exposure. Evaluate every control and record
  every violation, then choose the headline by precedence.
- **Filing a blocked order with zeroed metrics.** If the kill-switch branch reports notional
  and deviation as 0.0, the audit log no longer says what was actually stopped.
- **Releasing the kill switch quietly.** An unattributed release is the verbal-approval
  override the SFC called out, with the log line missing as well.
- **Treating the in-memory audit trail as the record.** It does not survive a restart, and
  paragraph 1.3.2(b) asks for 2 years.

## Verification

- Instantiate `HkSfcAlgorithmicTradingEngine(max_order_value_hkd=10_000_000.0, max_price_deviation_pct=5.0)`.
- Compliant long order (00700, 300.00 × 10,000, nominal 300.00): expect `SFC_COMPLIANT_APPROVED`,
  `order_value_hkd == 3_000_000.0`, `violations == ()`, `blocks_order` false.
- Order priced at 315.012 against a nominal of 300.00 (exactly 5.004%): expect
  `REJECTED_PRICE_DEVIATION_LIMIT`. At 315.00 (exactly 5.000%): expect approval.
- `market_last_price=0.0` and `market_last_price=None`: expect `REJECTED_MISSING_MARKET_DATA`
  with `price_deviation_pct is None` — not an exception, and not 0.0.
- Notional exactly on the limit through a value that drifts in binary floating point
  (100.04 × 10,000 against a limit of 1,000,400): expect approval.
- Covered, assured, flagged short sale of a Designated Security at the best ask: expect
  approval. Then flip one fact at a time and expect, respectively,
  `REJECTED_ILLEGAL_NAKED_SHORT`, `REJECTED_SHORT_SELL_ASSURANCE_MISSING`,
  `REJECTED_SHORT_SELL_NOT_FLAGGED`, `REJECTED_SHORT_SELL_NOT_DESIGNATED`,
  `REJECTED_SHORT_SELL_TICK_RULE` (99.99 against a best ask of 100.00), and
  `REJECTED_SHORT_SELL_ORDER_TYPE_NOT_PERMITTED` (a plain limit order in the CAS).
- Short sale with `short_sell_reference_price=None`: expect `MISSING_MARKET_DATA` and
  `is_short_sell_legal` false — the tick rule was not evaluated, so it was not satisfied.
- Oversized naked short: expect `REJECTED_ILLEGAL_NAKED_SHORT` as the headline with
  `ORDER_VALUE_LIMIT` also in `violations`, and `is_short_sell_legal` false.
- `trigger_sfc_kill_switch(reason=..., activated_by=..., scope="ALGO", key="HK_MOMENTUM_01")`:
  expect that algo blocked and every other algo approved; expect a `ValueError` when `reason`
  or `activated_by` is blank, and a CRITICAL log line on release.
- Run `python -m unittest discover -s skills/hong-kong-sfc-algorithmic-trading-guidelines/scripts`
  (87 tests) and confirm a 100% pass rate.

## Related Skills

- `hong-kong-exchange-hkex-orion-api`
- `shanghai-shenzhen-connect-programs`
- `execution-algorithm-kill-switch-integration`
- `risk-control-bypass-audit-logging`
- `wash-trade-and-spoofing-self-detection`
- `short-selling-borrow-cost-and-availability-modeling`
- `record-retention-periods-by-jurisdiction`
- `mas-singapore-algo-trading-guidelines`
- `uk-fca-algorithmic-trading-systems-controls`
- `sec-rule-15c3-5-risk-controls-us`
- `finra-algo-trading-registration-requirements`
