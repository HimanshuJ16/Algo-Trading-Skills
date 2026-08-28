# Workflows — position-limit-reporting-cftc-large-trader

Deep procedure reference. Regulatory citations and their sources are in
`references/standards.md`; this file is the operational sequence.

## 0. Resolve the levels for the evaluation date

Before any arithmetic, pin down four things and record them alongside the
report. An audit whose thresholds are not archived is not reproducible.

1. **Reporting level** for the commodity, from the 17 CFR 15.03(b) table.
2. **Which federal limits actually exist** for the contract. Only the nine
   legacy agricultural contracts carry federal single-month and
   all-months-combined limits. The other sixteen core referenced futures
   contracts carry a **spot-month limit only**; outside the spot month they are
   governed by exchange limits or accountability levels, which are not federal
   limits and are not this engine's business unless you configure them
   deliberately and label them as such.
3. **Today's spot-month level.** Some spot-month limits step down inside the
   spot month (the CFTC's published Live Cattle limit steps 600 → 300 → 200 as
   the contract approaches expiry). Re-resolve per business day; a level cached
   at the start of the month is wrong by the end of it.
4. **Which `contract_month` label is in its spot month.** The engine will refuse
   to run a configured spot-month test without it.

Leave a limit as `None` when it does not exist. `None` means *not tested* and
lands in `limits_not_tested`; it does not mean unlimited and must never be
approximated by a very large number.

## 1. Build the aggregation set (§ 150.4)

Decide, outside this engine, which accounts belong to the legal entity:
positions in any account whose trading the person directly or indirectly
controls, or in which the person holds a 10 percent or greater ownership or
equity interest, must be aggregated — subject to the § 150.4(b) exemptions
(limited-partner interests, independently operated owned entities, FCM
discretionary accounts, independent account controllers, underwriting,
broker-dealer activity, information-sharing restrictions, affiliate notice
filings). Several of those exemptions require a notice filing to rely on.

Then normalise the records:

- One record per `(account_id, contract_month, instrument_class)`. If the source
  system emits several rows for one account and month, consolidate them first —
  the engine raises on duplicates rather than adding them, because addition is
  how a double-counted account becomes a phantom breach or a phantom filing.
- Gross legs, both non-negative. Do not pass a signed short leg; the engine
  rejects it, because a negative short silently flips the net sign.
- One consistent `contract_month` spelling across every account. It is compared
  as an opaque string.
- `instrument_class` of `FUTURE` or `OPTION`. Options intended for the limit
  test must already be on a futures-equivalent basis — the engine does no delta
  conversion.
- `is_bona_fide_hedge` set only where you can actually defend the § 150.3
  claim. It suppresses the limit test, never the reporting test.

## 2. Reporting-level audit (§ 15.00(p)(1))

For each `(contract_month, instrument_class)` bucket:

```
reportable  <=>  gross_long >= level   OR   gross_short >= level
```

- **Each side independently.** Not `long + short`, not `|long - short|`.
- **Inclusive boundary.** "Equals or exceeds": exactly at the level is
  reportable.
- **Per future.** § 15.00(p)(1)(i) says "any one future" — never pool months.
- **Options separately.** § 15.00(p)(1)(ii) treats options exercising into the
  same future as their own bucket.
- **End of day.** The test is defined at "the close of the market on any
  business day". Running it on an intraday snapshot produces a number, but not
  the number the regulation defines.

`reportable_side` records which side triggered (`LONG`, `SHORT`, `BOTH`,
`NONE`), which is what you need when reconciling against the carrying firm's
view.

## 3. Federal limit audit (§ 150.2)

Drop bona fide hedge positions, then net long against short per contract month:

```
spot month        :  |net(spot_month)|      >  spot_month_limit
single month      :  |net(month)|           >  single_month_limit   for every month
all months combined: |sum(net(month))|      >  all_months_combined_limit
```

- **Exclusive boundary.** "In excess of": exactly at the limit is not a breach.
  This differs from step 2 by one contract, deliberately.
- **Only configured limits run.** Everything left `None` appears in
  `limits_not_tested`, and the audit note says in as many words that absence of
  a breach there is not evidence of compliance.
- **A configured spot-month limit with no `spot_month` raises.** A control that
  silently does not run is the worst outcome available.
- **Run this continuously.** § 150.2 prohibits *holding or controlling* an
  excess position; it is not a close-of-market test. Intraday spikes are
  breaches. (The reporting flag in step 2, by contrast, is end-of-day.)

## 4. Read the report correctly

- `is_reportable` and `is_limit_breached` are **independent flags**. `status`
  collapses to `SPECULATIVE_LIMIT_BREACHED` when both are true, so a caller
  reading only `status` loses the reportability. Read the flags.
- `month_detail` carries the per-bucket gross figures and the triggering side —
  this is the evidence, not the summary.
- `breaches` itemises each failed test with `limit_type`, `contract_month`,
  `net_position`, `limit` and `excess`.
- `limits_not_tested` is as important as `breaches`. A clean report on a
  crude-oil-shaped spec means "the spot-month limit was not exceeded", not "the
  position is within federal limits".
- `hedge_exempt_contracts_excluded` states how much exposure was withheld from
  the limit tests on your assertion.
- `ENGINE_DISABLED` asserts nothing about the positions. It is never evidence
  of compliance.

## 5. Escalation

A `SPECULATIVE_LIMIT_BREACHED` result is a live compliance event, not a
metric. Route it to the compliance function immediately and freeze
position-increasing orders in the affected commodity — see
`kill-switch-and-drawdown-circuit-breakers` and
`position-limit-breach-simulation-fire-drills` for the enforcement side, which
this engine deliberately does not perform.

A `FORM_102A_REPORTABLE` result is **not** an action item for you as a trader:
the carrying firm files. Use it to reconcile against what your FCM reports, to
anticipate a Form 40 special call (§ 18.04(a)), and to know that your positions
are now visible to the Commission at the entity level.
