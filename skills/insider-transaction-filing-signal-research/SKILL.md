---
name: insider-transaction-filing-signal-research
description: >-
  Use when building an equity factor from SEC Form 4 insider filings, scoring
  open-market purchases and sales on the EDGAR dissemination timestamp rather than the
  trade date and weighting by the filer's role.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: quant-research-alt-data
  tags: insider-trading-signal, sec-form-4, section-16, rule-10b5-1, point-in-time-data, alpha-factors, insider-sentiment
  brokers_frameworks: "SEC EDGAR Form 4; 17 CFR 240.16a-3; SEC Release 33-11138; EDGAR Ownership XML Technical Specification v3; Python standard library"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this when building an equity factor from **SEC Form 4** filings — the Section 16(a)
statement of changes in beneficial ownership filed by officers, directors and >10% beneficial
owners of a US registered class. The engine turns a stream of Form 4 transaction lines into a
point-in-time, role-weighted net insider sentiment score in $[-1, +1]$.

Two things make this harder than summing buys and sells, and the module exists for both:

- **The trade date is not the information date.** Rule 16a-3(g)(1) gives the filer until "the
  end of the second business day following the day on which the subject transaction has been
  executed." For plan trades where the insider does not pick the execution date, Rule
  16a-3(g)(2)–(4) deem the *broker-notification* date to be the execution date, capped at the
  third business day after the trade — so the lawful gap between execution and public filing
  reaches roughly five business days. Cohen, Malloy & Pomorski measured a median trade-to-report
  delay of 3 days across 1986–2007. A backtest keyed on `transaction_date` trades on information
  that did not exist. Every score here is taken as of a `filing_datetime` cut-off.

- **"Routine" is a property of the trader, not of the checkbox.** The 82 bps/month result rests
  on Cohen, Malloy & Pomorski's timing test — an insider who "placed a trade in the same calendar
  month for at least three consecutive years" is routine. Their 1986–2007 sample uses no 10b5-1
  information at all, and they report that "in unreported results we find very similar differential
  performance of opportunistic versus routine trades before 2000, suggesting that our results are
  not driven by trades in these plans." `classify_trader_regularity()` implements that test.

## When NOT to Use

- **Outside US Section 16.** Form 4 covers officers, directors and >10% beneficial owners of a
  class registered under Section 12. It has no bearing on UK PDMR notifications (UK MAR Art. 19),
  EU MAR managers' transactions, SEBI PIT Regulation 7(2) disclosures, or any non-US regime; those
  have different deadlines, different filers and different fields.

- **As a long-history backtest of the 10b5-1 filter.** The checkbox became mandatory only for
  reports **filed on or after 1 April 2023** (SEC Release 33-11138). Before that, "the disclosure
  of a purchase or sale under a Rule 10b5-1 trading arrangement in Forms 4 and 5 is voluntary,
  resulting in a lack of consistent and comprehensive information about such trades." An unchecked
  box in 2019 means *not disclosed*, not *not a plan trade*. Supply those records as
  `PlanStatus.UNKNOWN` and read `unknown_plan_status_count` before trusting any pre-2023 result.

- **As a claim that 10b5-1 trades are uninformative.** They are not — see Common Pitfalls. The
  default `exclude_plan_trades=True` buys a cleaner opportunistic subsample at the cost of
  discarding documented signal. That is a research choice, not a free lunch.

- **On derivative-only or compensation activity.** Grants (A), exercises (M, X), tax withholding
  (F) and gifts (G) are not directional open-market trades. They are counted and reported, never
  scored.

- **As an insider-trading compliance or surveillance control.** This reads public filings for
  alpha. For MNPI handling and alt-data governance see `insider-trading-controls-for-alternative-data-usage`;
  for market-abuse self-detection see `wash-trade-and-spoofing-self-detection`.

## Prerequisites

- Form 4 Table I transaction lines carrying, per line: `transaction_code` (General Instruction 8),
  `shares`, `price`, `transaction_date`, and the EDGAR **`filing_datetime`** at which the filing
  was disseminated. The filing timestamp is filing metadata, not a field inside the document —
  source it from the EDGAR index, not from `periodOfReport`.
- Reporting-owner relationship as the four independent EDGAR booleans `isDirector`, `isOfficer`,
  `isTenPercentOwner`, `isOther`, plus the free-text `officerTitle` (max 30 characters). These are
  **not** mutually exclusive: a founder-CEO on the board sets three of them.
- A determination of whether each P/S line actually executed on-market. Codes P and S read "Open
  market **or private** purchase/sale", so the code alone does not establish it.
- A role weight schedule. The bundled default (CEO/CFO 1.0, other officer 0.8, director 0.6,
  10% owner 0.3) is illustrative — no published source establishes these values.

## Workflow

1. **Ingest with both dates, and reject naive timestamps.**
   `filing_datetime` must be timezone-aware; the engine raises otherwise. EDGAR's Forms 3/4/5
   cut-off is 22:00 ET, and a naive instant silently adopts the host clock — which is how
   look-ahead bias enters a pipeline that looks correct.

2. **Evaluate as of an explicit instant.** `analyze_form4_filings(..., as_of=...)` scores only
   filings with `filing_datetime <= as_of`. Pass the whole history and roll `as_of` forward; the
   filings not yet public are counted in `not_yet_public_excluded_count`, not silently dropped.
   - **Decision point — do not "fix" the lag with a fixed +2-day offset on the transaction date.**
     Two business days is the *deadline*, not the observed delay, and Rule 16a-3(g)(2)–(4) legally
     extends it for plan trades. Late filings exist. Use the actual dissemination timestamp and
     read `max_trade_to_file_lag_days` to see what the feed really delivered.

3. **Decide the Rule 10b5-1 policy explicitly, per sample period.**
   - Post-2023-04-01 data: the checkbox is reliable; `PlanStatus.PLAN` / `NON_PLAN` are meaningful.
   - Pre-2023-04-01 data: supply `PlanStatus.UNKNOWN`. Then choose —
     `treat_unknown_plan_status_as_plan=True` drops them (conservative, costs most of the sample),
     `False` scores them and exposes the contamination through `unknown_plan_status_count`.
   - **Decision point — a regime boundary runs through 1 April 2023.** A backtest spanning it is
     comparing two different datasets. Split the sample there or report the two halves separately.

4. **Classify trader regularity from prior years only.** `classify_trader_regularity(history,
   classification_year=Y)` reads transactions dated strictly before `Y`, exactly as CMP designate
   insiders at the beginning of each calendar year. Three labels, not two: an insider who did not
   trade in each of the preceding years is `UNCLASSIFIED`, and CMP leave those out of the
   portfolio rather than calling them opportunistic.

5. **Resolve the Section 16 capacity from the flags, not from a role string.** The engine takes
   the highest-weighted applicable tier across `isOfficer` / `isDirector` / `isTenPercentOwner`,
   with the officer tier read out of the free-text title.
   - **Decision point — an unparseable role must not get a middle weight.** `unknown_role_weight`
     defaults to 0.0, excluding the trade from the score and logging it, rather than guessing.
     Every such record is counted in `unclassified_role_count`.

6. **Score, then apply the sample floors before reading a direction.** Weighted notional is
   $w \times \text{shares} \times \text{price}$; the score is
   $S = (\Sigma_{\text{buy}} - \Sigma_{\text{sell}}) / (\Sigma_{\text{buy}} + \Sigma_{\text{sell}}) \in [-1, +1]$.
   - **Decision point — the score is scale-free, so a single $1,000 purchase reads +1.00.** Set
     `min_total_notional_usd` and `min_distinct_insiders`; below either, the engine returns
     `INSUFFICIENT_DATA` rather than a saturated signal built on one trade.

7. **Reconcile the report before using it.** The exclusion counters plus the scored counts equal
   `filings_supplied` exactly. If they don't, records were lost upstream.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming Rule 10b5-1 sales carry no signal.** The SEC's own economic analysis in Release
  33-11138 documents -2.5% six-month industry-adjusted returns after the first sale under plans
  whose first trade fell within 30 days of adoption, and finds single-trade plans — 49% of the
  plans studied — "consistently loss-avoiding regardless of cooling-off period," avoiding declines
  up to -4%. Jagolinzer (2009) found plan participants' "sales systematically follow positive and
  precede negative firm performance." Filtering plan trades is a subsample choice; treating them
  as noise by definition is wrong.

- **Reading an unchecked 10b5-1 box on a pre-April-2023 filing as `NON_PLAN`.** Disclosure was
  voluntary until then. The SEC's own footnote on a study using 2003–2006 Form 4 data warns that
  "trades classified as 'non-10b5-1' trades in the study may include 10b5-1 plan trades."

- **Aligning the signal to `transaction_date`.** The market cannot act on a trade before EDGAR
  disseminates it. This is the look-ahead bias that makes an insider factor backtest well and
  trade badly, and it is invisible in the equity curve.

- **Adding a flat +2 business days instead of using the filing timestamp.** Rule 16a-3(g)(2)–(4)
  legally deems the broker-notification date to be the execution date for plan trades, capped at
  the third business day after the trade — so a compliant filing can land ~5 business days out.
  Late filings exist beyond that.

- **Treating a single role string as the filer's capacity.** `isDirector`, `isOfficer`,
  `isTenPercentOwner` and `isOther` are four independent booleans; there is no structured "CEO"
  field anywhere on Form 4. The title is 30 characters of free text, so `"Chairman, CEO & Pres"`,
  `"Chief Executive Officer"` and `"C.E.O."` are the same person's capacity spelled three ways.

- **Matching transaction codes with `code == "S"`.** General Instruction 8 directs filers to report
  equity-swap-linked trades as `"S/K"` or `"P/K"`. A bare equality test drops every one of them
  without a trace.

- **Treating code P as proof of an open-market trade.** The SEC's own definition is "Open market
  **or private** purchase of non-derivative or derivative security."

- **Double-counting Form 4/A amendments.** An amendment restates transaction lines under a new
  accession number. A feed carrying the original and the amendment counts the same economic trade
  twice; the engine warns on a repeated `filing_id` but cannot resolve amendment chains for you.

- **Equal-weighting a 10% beneficial owner and a CEO.** An outside fund crossing 10% files the
  same form as an officer with direct operational visibility, and its trade is usually a portfolio
  decision about itself, not a view on the issuer.

## Verification

- Instantiate `InsiderFilingSignalEngine()`. Audit a CEO open-market purchase (10,000 shares @
  $50.00, `plan_status=PlanStatus.NON_PLAN`, `is_officer=True`, `officer_title="Chief Executive
  Officer"`) whose `filing_datetime` is **after** `as_of` ⟹ verify
  `not_yet_public_excluded_count == 1` and `signal_classification == INSUFFICIENT_DATA`. Advance
  `as_of` past the dissemination instant ⟹ verify `STRONG_BULLISH_OPPORTUNISTIC_BUY` at
  $S = +1.00$. This is the point-in-time gate; it is the single most important behaviour here.
- Audit a disclosed 10b5-1 sale ⟹ verify `routine_10b5_1_filtered_count == 1` and
  `INSUFFICIENT_DATA`; re-run with `exclude_plan_trades=False` ⟹ verify it scores at $S = -1.00$.
- Audit an insider who traded every March for three consecutive prior years ⟹ verify
  `classify_trader_regularity` labels them `ROUTINE`, and that the engine excludes their trades
  when the labels are supplied.
- Confirm the report reconciles: exclusion counters + scored counts == `filings_supplied`.
- Run `python -m unittest discover -s skills/insider-transaction-filing-signal-research/scripts`.

## Related Skills

- `insider-trading-controls-for-alternative-data-usage`
- `earnings-call-transcript-nlp-signal-research`
- `point-in-time-fundamentals-data-joins`
- `lookahead-bias-elimination`
- `survivorship-bias-free-universe-construction`
