# Standards — options-flow-unusual-activity-detection

## Detection thresholds (library defaults — calibrate before use)

These are this library's defaults, **not** industry standards. No regulator, exchange
or standards body publishes an "unusual options activity" threshold; every commercial
scanner uses its own. Calibrate them per underlying and per liquidity tier and record
the rationale.

| Parameter | Default | What it actually does |
|---|---|---|
| `min_v_oi_ratio` | $1.5\times$ | Print size relative to the series' standing open interest. Above $1.0$ at least part of the print must be opening new interest — a weak proxy for "new position", not a measurement of one. |
| `min_v_adv_ratio` | $2.0\times$ | Print size relative to the series' average daily volume. Guards against the $V/OI$ gate firing on 0DTE and newly listed series whose OI is naturally near zero. |
| `min_premium_usd` | $\$100{,}000$ | Notional premium $V \times P \times M$. A size filter for "plausibly institutional"; routine on index and mega-cap names, unreachable on thin single names. |
| `contract_multiplier` | $100$ (per trade) | Premium multiplier of the **series**, supplied per print rather than assumed. |

All three size gates must clear before a print is flagged. A gate whose input was not
supplied (`None`) is recorded in `gates_unevaluable` and can never clear.

## What the consolidated options feed carries (verified against the primary spec)

Source: **OPRA Binary Data Recipient Interface Specification**
([opraplan.com](https://cdn.opraplan.com/documents/OPRA_Pillar_Output_Specification.pdf)).

| Fact | Location |
|---|---|
| The Equity and Index Last Sale message contains: Message Header, Security Symbol, Expiration Block, Strike Price Denominator Code, Strike Price, Volume, Premium Price Denominator Code, Premium Price, Trade Identifier, Trading Session Identifier. **No aggressor side, no open/close indicator.** | Sec. 6.01 |
| Open Interest Volume is carried in the Equity and Index **End of Day Summary** message, transmitted shortly before the Good Night messages — not in the trade stream. | Sec. 6.03 |

Consequences: side must be *inferred* from the quote, and open interest is a
prior-session quantity. OCC computes open interest overnight from cleared positions and
publishes it for the following session; it does not update intraday
([OIC / OCC investor FAQ](https://www.optionseducation.org/referencelibrary/faq/general-information):
"OCC can only report new open interest after clearing and pairing opening and closing
positions at the end of the day").

Buy/sell **and** open/close broken down by participant type (customer, professional
customer, broker-dealer, market maker) is a separate commercial product — the
[Cboe Open-Close Volume Summary](https://datashop.cboe.com/cboe-open-close-volume-summary),
available EOD or as 1-/10-minute intraday snapshots for the Cboe exchanges (BZX, C1,
C2, EDGX). If a strategy depends on knowing whether a print opened a position, that
product — not a V/OI heuristic — is the answer.

## Trade-side inference (quote rule) accuracy

Source: Savickas, R. & Wilson, A. J. (2003), **"On Inferring the Direction of Option
Trades"**, *Journal of Financial and Quantitative Analysis* 38(4), 881–902
([Cambridge Core](https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/abs/on-inferring-the-direction-of-option-trades/FDA4541B57F78B2C8DCE129AFC25AAF0)).

| Method | Share of *classifiable* trades signed correctly |
|---|---|
| Quote rule (used here) | 83% |
| Lee–Ready | 80% |
| Ellis–Michaely–O'Hara | 77% |
| Tick test | 59% |

Measured against proprietary CBOE data with known trade direction. Additional findings
that shape this module's behaviour:

- Midspread trades are **not classifiable** by the quote rule — hence the explicit
  `MID_MARKET` / `direction_is_inferred = False` outcome rather than a coin-flip label.
- Outside-quote and reversed-quote trades are significantly misclassified by all four
  methods — hence `UNCLASSIFIED` on a crossed quote instead of a guess.
- Accuracy varies with trading frequency, trade size, moneyness and maturity.
- Excluding index-option complex (multi-leg) trades, 15% of the sample, lifted the
  quote rule above 87%.

## Contract multiplier

OCC contract adjustments following splits, mergers, spin-offs and special distributions
may modify a series' deliverable, strike, **contract multiplier** and symbol
([Fidelity Learning Center — Option contract adjustments](https://www.fidelity.com/learning-center/investment-products/options/contract-adjustments);
adjustment memos are published by OCC at [infomemo.theocc.com](https://infomemo.theocc.com/)).
Non-US markets set their own contract sizes. The multiplier is therefore a per-series
input to the premium calculation, never a constant.

Note the distinction: an adjusted series often keeps a 100 premium multiplier while its
*deliverable* changes (e.g. 150 shares after a 3-for-2 split). Premium here is computed
from the premium multiplier; do not substitute the deliverable share count.

## Known limitations

- Every directional label is an inference from the quote rule, with the error rate
  above. Aggregate across many prints; do not act on one.
- Opening vs closing is unknowable from the trade feed; `UNUSUAL_*_BLOCK` labels assume
  an opening trade the data cannot confirm.
- Multi-leg and delta-hedged flow prints leg by leg with no linkage and is scored as if
  each leg were standalone directional risk.
- Scoring is per-print. Cumulative session volume, cross-venue sweep reconstruction, and
  time-clustered repeat prints in the same series are out of scope.

## Category

`quant-research`
