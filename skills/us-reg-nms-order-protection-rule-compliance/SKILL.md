---
name: us-reg-nms-order-protection-rule-compliance
description: >-
  Use when an execution in a US NMS stock must be tested against the protected
  quotations displayed at the moment it printed — SEC Regulation NMS Rule 611.
  Covers the definition that actually governs (Rule 600(b)(105) makes a
  trade-through a price test, not a side test, and confines it to 09:30–16:00
  ET), the per-venue one-second flickering-quote exception of Rule 611(b)(8),
  the Rule 600(b)(47)(ii) sweep obligation behind an ISO marking, Self-Help
  declarations as time intervals, and the crossed-market, auction, benchmark
  and stopped-order exceptions.
domain: US Regulatory Compliance & Market Structure
subdomain: SEC Regulation NMS (Rule 611 Order Protection)
tags:
- sec-reg-nms
- rule-611
- trade-through
- protected-nbbo
- iso-orders
- self-help
- market-structure
- finra-cat
brokers_frameworks:
- 17 CFR 242.611 (Order Protection Rule)
- 17 CFR 242.600(b) (Reg NMS definitions)
- SEC Division of Trading and Markets Reg NMS Rule 610/611 FAQ
- FINRA CAT (Consolidated Audit Trail) / CAT NMS Plan
- FIX ExecInst (tag 18) = 'f' Intermarket Sweep (FIX 5.0 SP2; carried as a venue
  extension on earlier FIX sessions)
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when an execution in an **NMS stock** must be reviewed against
the **protected quotations** that were displayed when it printed, under
**SEC Regulation NMS Rule 611** (17 CFR 242.611). That is the surveillance
obligation in Rule 611(a)(2): a trading centre must "*regularly surveil to
ascertain the effectiveness*" of its trade-through policies and procedures and
"*take prompt action to remedy deficiencies*".

Use it as well when reviewing existing Rule 611 code, because three readings
are widely copied and each is wrong in a direction that hides violations:

- **"A buy trades through the offer, a sell trades through the bid."**
  Rule 600(b)(105) defines a trade-through as "*the purchase or sale of an NMS
  stock during regular trading hours ... at a price that is lower than a
  protected bid or higher than a protected offer*". It is a price test. A
  *purchase* printed below the protected bid trades through that bid — which is
  exactly why Rule 611(b)(9) exists to except a stopped buy order printed below
  the national best bid. Testing by side passes half the universe silently.
- **"A quote changed within the last second, so Rule 611(b)(8) applies."** The
  exception is about *the venue whose protected quotation was traded through*
  and about a price *equal or inferior* to the print. Read loosely, it excepts
  essentially every trade-through in a liquid name, and the engine reports
  nothing, ever.
- **"Compare the print against the current NBBO."** FAQ 3.02 requires trade
  prices to be compared with protected quotations *at the time of execution*;
  FAQ 6.01 assesses a firm on the quotation data it held then. An unordered
  quote list fed to `max`/`min` lets a quotation that did not yet exist decide
  the outcome.

**Jurisdiction: United States only.** Rule 611 is an SEC rule for NMS stocks.
Nothing here transfers to EU/UK best execution (MiFID II RTS 27/28, which is an
execution-quality obligation, not a price-priority prohibition), to Canadian
UMIR order protection, or to any Asian market.

**Rule status, 2 September 2026.** Rule 611 is in effect. On 11 June 2026 the
Commission proposed to rescind it in its entirety, along with Rule 610(e) and
the definitions at Rule 600(b)(6), (7), (47), (54), (81), (82) and (105) —
Release No. 34-105655, File No. S7-2026-20, 91 FR 36656 (17 June 2026),
comments closed 17 August 2026. **No final rule has been adopted**, so the
obligation stands unchanged. Track the file rather than pre-emptively removing
controls.

## When NOT to Use

- **As a pre-trade router control.** This is post-trade surveillance. What
  Rule 611(a)(1) requires is *written policies and procedures reasonably
  designed to prevent* trade-throughs; a detector that runs after the print
  demonstrates the surveillance limb of Rule 611(a)(2), not the prevention limb.
- **As the firm's Rule 611 policies and procedures.** Rule 611 compliance is a
  policies-and-procedures standard, not a per-trade pass/fail. An engine output
  is evidence for that standard, never a substitute for it.
- **For listed options, or for any non-NMS-stock instrument.** Rule 611 reaches
  NMS *stocks*. Options, futures, fixed income and FX are outside it entirely.
- **Outside regular trading hours.** Rule 600(b)(105) confines trade-throughs to
  09:30–16:00 ET, and FAQ 7.01 states that policies and procedures "*are not
  required to address trades that occur outside of regular trading hours, and
  the exceptions in Rule 611(b), including the ISO exception, are not needed*"
  there. The engine returns `NOT_SUBJECT_RULE_611`; do not read that as
  "compliant by exception".
- **As a depth-of-book model.** Rule 600(b)(81) protects only a quotation that
  is the *best* bid or offer of a national securities exchange or national
  securities association, displayed by an automated trading centre and
  disseminated under an effective NMS plan. Depth behind the BBO is never
  protected. One top-of-book record per venue is the correct granularity;
  feeding L2 levels in manufactures protected quotations that do not exist.
- **To decide the Rule 611(b)(2), (b)(3) or (b)(7) exceptions.** Whether a
  contract was "regular way", whether a print was a single-priced auction, and
  whether a benchmark price was "*not based, directly or indirectly, on the
  quoted price ... and for which the material terms were not reasonably
  determinable at the time the commitment to execute the order was made*" are
  facts about the transaction. The engine records the claim and marks it as
  asserted; it cannot verify it from quote data, and neither can you.

## Prerequisites

- Python 3.9+. No third-party dependencies. `zoneinfo` needs a system tz
  database — on Windows and slim containers, `pip install tzdata`, or the
  regular-trading-hours test raises rather than guessing at Eastern time.
- **Firm-specific quotation data**, timestamped as *the firm received it*. Per
  FAQ 6.01 a firm's Rule 611 compliance "*will be assessed based on the time
  that orders and quotations are received, and trades are executed, at that
  trading center*", not on Network (SIP) timestamps. SIP data is the common
  reference regulators screen with (FAQ 6.04) — expect it to produce false
  positives against your own book, and be able to explain them.
- **Automated/manual quote status per venue.** Rule 611 protects automated
  quotations only (Rule 600(b)(6), (54), (81)). A trading centre that cannot
  display automated quotations must identify its quotations as manual, at which
  point they may be traded through freely.
- **Clocks synchronised to CAT tolerance.** The CAT NMS Plan requires Industry
  Members to hold Business Clocks within **50 ms** of NIST (Participants within
  **100 µs**; clocks used solely for Manual Order Events within one second), and
  timestamps reported in **milliseconds or finer**, using the finest increment
  the firm's own order-handling systems capture, truncated — never rounded — at
  nanoseconds.
- **Time of execution as defined for Rule 611.** FAQ 3.02: the time "*when
  final agreement is reached on the stock, price, and size of the trade*",
  documented simultaneously and not subject to retrospective alteration. Not the
  time the trade was reported.

## Workflow

1. **Gate on the session first.** Convert the execution timestamp to Eastern
   time and test it against 09:30–16:00. Outside that window Rule 611 does not
   reach the print at all and no exception is needed — evaluating it produces
   fictitious violations out of pre- and post-market prints, which is where
   wide spreads live.
2. **Take the book as of the execution, per venue.** Discard quotes stamped
   after the execution; for each venue keep only its most recent quote at or
   before it. Filter to the execution's own symbol — a mixed feed produces a
   numerically valid, entirely meaningless NBBO, and does it silently.
3. **Drop what is not protected.** Manual quotations, and venues under an open
   Self-Help declaration *as of the execution time*. Self-Help must be stored as
   an interval, not a boolean: replaying yesterday's tape must not depend on
   which venues are broken today.
4. **Classify by price, not by side.** `price > protected offer` trades through
   the offer; `price < protected bid` trades through the bid. Apply both tests
   to buys and to sells. Record which protected quotation was hit and which
   venue displayed it — the exception analysis that follows is per-venue.
5. **Separate a Self-Help exemption from a clean fill.** Compute the outcome
   against the full protected market *and* against the market with Self-Help
   venues removed. A print that is clean only after the removal is
   `EXEMPT_SELF_HELP` under Rule 611(b)(1), not `COMPLIANT`. That distinction is
   the whole audit value: it is the count an examiner will ask about.
6. **Apply the exceptions only to a transaction that was a trade-through.**
   Rule 611(b) excepts "*the transaction that constituted the trade-through*".
   An ISO-marked execution that never traded through the market is compliant,
   not exempt; classifying it as exempt inflates your reliance on the ISO
   exception in your own records.
7. **Take crossed markets out first.** When a protected bid is priced above a
   protected offer, every price is through one side or the other.
   Rule 611(b)(4) excepts the condition rather than asking the trading centre to
   resolve it. A locked market (NBB == NBO) is *not* crossed and gets no
   exception.
8. **Test the ISO sweep, do not trust the marking.** The receiving trading
   centre may rely on the marking (Rule 611(b)(5)). The router may not:
   Rule 611(c) requires it to take reasonable steps to establish that the order
   met Rule 600(b)(47), which obliges simultaneous ISOs against the **full
   displayed size of every protected quotation priced superior to the ISO's
   limit price** — protected offers for a buy, protected bids for a sell. Feed
   the limit price and the routed venues in and the obligation is checkable.
   Venues under Self-Help may be left out of the sweep (FAQ 4.09).
9. **Run the flickering-quote test per venue and strictly backwards.** Only the
   venue whose protected quotation was traded through counts, only quotes in the
   one second *before* the execution count, and the quote must be *equal or
   inferior* to the print — a higher offer where the offer was traded through, a
   lower bid where the bid was. The quote in force *at* the execution is the one
   that was traded through and can never support the exception.
10. **Check the stopped-order condition you can check.** Rule 611(b)(9)(iii)
    requires the print to be "underwater": for a stopped **buy** order, lower
    than the national best bid; for a stopped **sell** order, higher than the
    national best offer. Customer account and order-by-order agreement are
    assertions; the price test is not.
11. **Record the snapshot on every outcome, exceptions included.** An audit
    record for an exempt execution that zeroes the NBBO cannot be reconciled
    against CAT and cannot be defended. Retain the protected NBB/NBO, the
    as-of instant, the contributing venues, the Self-Help venues, and whether
    Self-Help notice was recorded.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Testing buys against the offer and sells against the bid.** Rule 600(b)(105)
  is a price test. A purchase below the protected bid is a trade-through of that
  bid. The side-based test reports it clean, and the existence of the
  Rule 611(b)(9) stopped-order exception — which excepts exactly that print —
  is the proof that it is not clean.
- **Reading Rule 611(b)(8) as "any quote updated within one second".** In a
  liquid NMS stock every protected venue re-quotes many times per second, so the
  loose test excepts a 100 bps trade-through as readily as a one-tick one. The
  exception is per-venue, strictly prior, and requires an *equal or inferior*
  price from the venue that was traded through.
- **Evaluating against the current NBBO, or against an unordered quote list.**
  Both leak quotations that did not exist at execution. In an audit tool that is
  look-ahead bias with an examiner attached: the same execution replayed twice
  gives different answers.
- **Treating Self-Help as a boolean on the engine.** A live flag makes a
  historical evaluation depend on today's operational state. Store declarations
  as `(declared_at, revoked_at)` intervals and evaluate at the execution time.
- **Declaring Self-Help without sending notice.** FAQ 4.07 sets three mandatory
  elements — **notice**, **systems assessment and response**, **objective
  parameters** — and notice to the bypassed trading centre "*must be sent
  immediately upon use of the exception*". A declaration with no notice record
  is a deficiency in the policies and procedures, whatever the trade evaluation
  says. The FAQ also names the objective parameter the Commission had in mind:
  repeated failure of the destination to turn an IOC around within one second,
  *after adjusting for order transmission time*.
- **Bypassing a venue before declaring.** Executing through a lagging venue's
  quote and back-dating the Self-Help declaration produces exactly the record an
  examiner is looking for. FAQ 4.07 is also explicit that a router is *not*
  entitled to elect Self-Help when it has reason to believe the problem is its
  own systems or connections.
- **Trusting an ISO marking end to end.** Rule 611(b)(5) relieves the receiving
  trading centre. It does not relieve the router, whose Rule 611(c) obligation
  runs to Rule 600(b)(47)(ii): simultaneous ISOs against the full displayed size
  of every superior-priced protected quotation. Note the direction — superior to
  the ISO's **limit price**, not to its execution price — and note that an ISO is
  by definition a **limit** order.
- **Treating a VWAP tag as the Rule 611(b)(7) exception.** FAQ 3.16 makes the
  benchmark exception facts-and-circumstances: whether the price was not based
  on the quoted price and whether the material terms were reasonably
  determinable at commitment. A boolean flag records a claim. FAQ 3.08 requires
  the firm to retain documentation of the externally observable circumstances
  behind any adjustment factor.
- **Flagging a crossed market as a violation.** NBB above NBO makes every price
  a trade-through. Rule 611(b)(4) excepts it. Without that branch, every crossed
  instant floods the surveillance queue with noise.
- **Flagging auction prints.** Rule 611(b)(3) excepts single-priced opening,
  reopening and closing transactions — which are a large share of daily volume
  and routinely print away from the contemporaneous NBBO.
- **Letting a NaN price through.** Every `<` and `>` comparison against NaN
  returns `False`, so a bad tick is reported as a clean compliant execution.
  A data-quality failure must raise, never resolve to "no violation".
- **Mixing naive and timezone-aware timestamps.** Subtracting them raises
  `TypeError`, and it will be the one record whose feed carried a zone that
  crashes the overnight batch.
- **Asserting a six-year retention period.** Information required to be reported
  to CAT is maintained under **SEA Rule 17a-4(b)** — three years, the first two
  in an accessible place. Business Clock synchronisation logs run **five**
  years. The six-year figure belongs to Rule 17a-4(a) blotters and ledgers, not
  to Rule 611 surveillance records.

## Verification

- Confirm the price test is side-independent: a **BUY** at `$99.50` against
  NBB `$100.00` / NBO `$100.05` must return `TRADE_THROUGH_VIOLATION` with
  `trade_through_kind == THROUGH_PROTECTED_BID`, and a **SELL** at `$100.60`
  must return `THROUGH_PROTECTED_OFFER`.
- Confirm the session gate: an execution at 08:00 ET, five dollars through the
  market, returns `NOT_SUBJECT_RULE_611` with `is_regular_trading_hours` False —
  and does so with no quotes supplied at all.
- Confirm the flickering-quote exception does not swallow surveillance: with
  every venue quoting *as of* the execution instant, a `$101.00` print against a
  `$100.05` protected offer must still be `TRADE_THROUGH_VIOLATION`. Then
  confirm it fires correctly when the traded-through venue itself showed
  `$100.09` half a second earlier, and does *not* fire when the inferior quote
  came from a different venue.
- Confirm as-of selection: a quote stamped one second *after* the execution must
  not enter the protected NBBO, and a superseded quote from the same venue must
  not survive its replacement.
- Confirm Self-Help is time-scoped: declare at T−10m, revoke at T−5m, then
  evaluate the same execution at T−9m (`EXEMPT_SELF_HELP`) and at T
  (`TRADE_THROUGH_VIOLATION`).
- Confirm the ISO sweep check: a buy ISO with limit `$100.30` against protected
  offers of `$100.05` (NYSE) and `$100.10` (NASDAQ) must return `EXEMPT_ISO`
  when both are routed and `ISO_SWEEP_NOT_SUBSTANTIATED`, naming NASDAQ, when
  only NYSE is.
- Confirm the stopped-order test: a stopped **buy** below the protected bid is
  `EXEMPT_STOPPED_ORDER`; the same order above the protected offer is not.
- Confirm the input guards: a NaN price, an infinite price, a negative price, a
  zero quantity, a string `side`, and quotes for a different symbol must each
  raise `RegNMSError` rather than return an audit result.
- Run the test suite:
```bash
cd skills/us-reg-nms-order-protection-rule-compliance/scripts
python -m unittest discover -s skills/us-reg-nms-order-protection-rule-compliance/scripts
```

## Related Skills

- `sec-rule-15c3-5-risk-controls-us`
- `us-reg-sho-short-sale-locate-requirements`
- `smart-order-routing-across-venues`
- `best-execution-record-keeping-global`
- `wash-trade-and-spoofing-self-detection`
- `clock-synchronization-ptp-for-trading-hosts`
