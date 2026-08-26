# Workflows for SEC Form 4 Insider Filing Signal Research

Deep procedure for `InsiderFilingSignalEngine`. Jurisdiction: United States, Exchange Act
Section 16. See `standards.md` for the authorities behind each rule cited here.

## 0. Decide the sample period before anything else

The dataset changes character on **1 April 2023**, the compliance date for the amended Forms 4
and 5. Before it, Rule 10b5-1 designation was voluntary and inconsistently supplied. After it,
the checkbox is mandatory and the plan adoption date appears in "Explanation of Responses".

| Sample period | `plan_status` to supply | Usable analysis |
|---|---|---|
| Filed on/after 2023-04-01 | `PLAN` / `NON_PLAN` from the checkbox | Both the plan filter and the CMP classifier |
| Filed before 2023-04-01 | `UNKNOWN` unless the plan was affirmatively disclosed | The CMP classifier; the plan filter is unreliable |

A study spanning the boundary is comparing two datasets. Split it, or report both halves.

## 1. Ingestion

Per Form 4 Table I transaction line, capture:

- `filing_id` — accession number. Form 4/A amendments carry a *different* accession restating the
  same economic trade; resolve amendment chains here, not downstream. The engine warns on a
  repeated `filing_id` but cannot reconstruct which line supersedes which.
- `transaction_code` — General Instruction 8 code, verbatim, including combined forms like `"S/K"`.
- `shares`, `price` — finite and strictly positive. Direction is carried by the code. A feed that
  encodes sales as negative quantities must be normalised before it reaches the engine, which
  rejects non-positive values rather than guessing.
- `transaction_date` — execution date, from `transactionDate` in the ownership XML.
- `filing_datetime` — **timezone-aware** EDGAR dissemination instant. This is filing metadata from
  the EDGAR index, not a field in the document. `periodOfReport` is the date of the earliest
  transaction and is not a substitute.
- `is_director`, `is_officer`, `is_ten_percent_owner`, `is_other` — the four independent
  `reportingOwnerRelationship` booleans, copied as-is. Do not collapse them into one role.
- `officer_title` — the raw 30-character free-text title.
- `is_open_market` — a separate determination. Codes P and S both read "open market **or private**".

## 2. Point-in-time evaluation

```python
report = engine.analyze_form4_filings(
    symbol="AAPL",
    filings=all_form4_lines,          # full history is fine
    as_of=datetime(2024, 6, 30, 20, 0, tzinfo=ZoneInfo("America/New_York")),
    lookback_days=90,                  # optional: only recently disseminated filings
    trader_regularity=labels,          # optional: CMP labels
)
```

Roll `as_of` forward across the backtest and re-evaluate. Filings not yet disseminated at that
instant land in `not_yet_public_excluded_count`; they are excluded, never an error, so the same
input list works for every date in the walk.

**Do not** substitute `transaction_date + 2 business days`. Rule 16a-3(g)(2)–(4) deems the
broker-notification date to be the execution date for plan trades, capped at the third business
day after the trade, so a compliant filing can be public ~5 business days out — and late filings
exist beyond that. Read `max_trade_to_file_lag_days` to see what the feed actually delivered.

## 3. Cohen-Malloy-Pomorski trader classification

```python
labels = classify_trader_regularity(prior_years_of_filings, classification_year=2024)
```

The classifier reads only transactions dated strictly before `classification_year`, mirroring
CMP's convention of designating insiders at the start of each calendar year from prior history.
Three outcomes:

| Label | Condition |
|---|---|
| `ROUTINE` | Traded in the same calendar month in each of the 3 preceding years |
| `OPPORTUNISTIC` | Traded in each of the 3 preceding years, no repeated calendar month |
| `UNCLASSIFIED` | Missing a trade in one or more of the preceding years |

CMP exclude `UNCLASSIFIED` insiders from the portfolio rather than treating them as opportunistic.
The engine keeps their trades when labels are supplied — filter them out yourself if you are
reproducing CMP's portfolio construction.

Re-run the classification once per calendar year. Reusing one year's labels across a decade
leaks later trading behaviour backwards.

## 4. Filter order inside the engine

Each in-scope filing passes through these gates in order, falling into exactly one bucket:

1. **Point-in-time** → `not_yet_public_excluded_count`, then `outside_lookback_excluded_count`
2. **Rule 10b5-1 policy** → `routine_10b5_1_filtered_count` (`unknown_plan_status_count` is a
   diagnostic overlay recorded here, not a bucket)
3. **CMP routine trader** → `routine_trader_filtered_count`
4. **Open-market** → `non_open_market_excluded_count`
5. **Transaction code** → `non_purchase_sale_code_count` (+ `non_purchase_sale_codes_seen`)
6. **Scored** → `opportunistic_buys_count` / `opportunistic_sales_count`

The six buckets sum to `filings_supplied`. Assert that in your pipeline; a mismatch means records
were lost before the engine saw them.

## 5. Role weighting

Capacity resolves to the highest-weighted applicable tier:

```
is_officer            -> officerTitle matched: CEO (1.0) | CFO (1.0) | OTHER_OFFICER (0.8)
is_director           -> DIRECTOR (0.6)
is_ten_percent_owner  -> TEN_PCT_OWNER (0.3)
none of the above     -> unknown_role_weight (default 0.0) + WARNING + counter
```

The title is 30 characters of free text. The bundled patterns cover `CEO`, `C.E.O.`,
`Chief Executive…`, `CFO`, `Chief Financial…`. Everything else — `COO`, `Chief Accounting
Officer`, `General Counsel`, `EVP` — falls to `OTHER_OFFICER` by design; add patterns rather than
adding a weight tier the schedule does not document.

Weights are illustrative. Override via the constructor and recalibrate per universe:

```python
engine = InsiderFilingSignalEngine(role_weights={"TEN_PCT_OWNER": 0.0})
```

Overrides are copied into a read-only mapping, so nothing the caller does afterwards can mutate
engine state.

## 6. Scoring

$$S = \frac{\sum_{\text{buys}} w \cdot q \cdot p - \sum_{\text{sells}} w \cdot q \cdot p}{\sum_{\text{buys}} w \cdot q \cdot p + \sum_{\text{sells}} w \cdot q \cdot p}$$

With non-negative weights and strictly positive quantities and prices, $S \in [-1, +1]$ by
construction; the engine clamps and rounds to 4 dp so a float artefact cannot escape the bound.

Classification order — floors first, direction second:

1. Zero total weighted flow → `INSUFFICIENT_DATA`
2. Scored notional below `min_total_notional_usd` → `INSUFFICIENT_DATA`
3. Fewer than `min_distinct_insiders` scored insiders → `INSUFFICIENT_DATA`
4. $S \ge$ `bullish_threshold` → `STRONG_BULLISH_OPPORTUNISTIC_BUY`
5. $S \le$ `bearish_threshold` → `BEARISH_OPPORTUNISTIC_SELL`
6. Otherwise → `NEUTRAL`

The floors matter because $S$ is scale-free. Without them a single $1,000 purchase and a
$50,000,000 multi-officer bid both return $+1.00$. An insider is counted toward
`distinct_insiders_count` only if their weight is above zero.

## 7. Interpreting the score

$S$ measures the *balance* of weighted opportunistic flow, not its size. Pair it with
`total_opportunistic_buy_notional_usd`, `distinct_insiders_count` and a market-cap normalisation
before cross-sectional ranking; the raw ratio is not comparable across a microcap and a megacap.

Note the asymmetry the literature reports: Lakonishok & Lee (2001) find the predictive content of
insider trades concentrated in purchases, with sales largely uninformative — insiders sell for
diversification, liquidity and tax reasons that have nothing to do with a view. CMP's opportunistic
long-short spread does load on both legs. A symmetric score is a modelling choice; test a
buy-only variant on your universe before assuming the sell leg helps.
