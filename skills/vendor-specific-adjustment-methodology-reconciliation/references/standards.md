# Institutional Vendor Corporate Action Adjustment Standards

All factor conventions below are sourced from vendor and academic-database documentation;
see [§5 Sources](#5-sources). Where vendors genuinely disagree, the disagreement is stated
rather than averaged away — that disagreement is what this skill exists to reconcile.

## 1. The three independent adjustment axes

Vendors do not expose a single "adjusted / unadjusted" switch. Bloomberg's historical data
overrides expose three separate booleans, and every other vendor convention in this skill is
a particular setting of the same three axes:

| Axis | Covers | Adjusts price | Adjusts volume |
| :--- | :--- | :--- | :--- |
| **Share-count change** | Stock splits, reverse splits/consolidations, stock dividends/bonus | Yes | **Yes** |
| **Ordinary cash** | Regular cash, interim, income, distribution, interest on capital | Yes | No |
| **Abnormal cash** | Special cash, liquidation, capital gains, return of capital, spin-offs, rights | Yes | No |

The volume column is the axis most often implemented incorrectly. CRSP states it explicitly:
"Split events always include stock splits, stock dividends, and other distributions with
price factors such as spin-offs, stock distributions, and rights. **Shares and volumes are
only adjusted using stock splits and stock dividends.**" Xignite/QUODD states the same rule
from the other direction: "Volume is only adjusted for the corporate events that change the
shares outstanding of the security on the EX date... but does not require adjustment for the
corporate actions such as cash dividend and spin-offs."

## 2. Vendor Adjustment Methodology Classification Matrix

| Methodology enum | Share-count axis | Ordinary cash axis | Abnormal cash axis | Notes |
| :--- | :---: | :---: | :---: | :--- |
| `CRSP_TOTAL_RETURN` | on | on | on | Total-return convention: dividends folded into the price series proportionally. |
| `BLOOMBERG_PROPORTIONAL` | on | on | on | Equivalent to Bloomberg `adjustmentSplit` + `adjustmentNormal` + `adjustmentAbnormal` all true. |
| `SPLIT_ONLY_PRICE_RETURN` | on | **off** | on | Price-return convention: "adjusted for all the corporate actions except the adjustment for ordinary cash dividends" (Xignite `PriceReturn`). Special dividends and spin-offs still adjust price. |
| `RAW_UNADJUSTED` | off | off | off | Raw exchange prints. |

> **Naming caveat — read before citing CRSP.** `CRSP_TOTAL_RETURN` denotes the *total-return
> convention*, not CRSP's own adjusted-price field. CRSP's Factor to Adjust Price (`FACPR`,
> cumulated as `CFACPR`) is **set to zero for ordinary cash dividends**; `PRC / CFACPR` is
> therefore a split-adjusted, *price-return* series, and CRSP delivers dividend income through
> its return series instead. Xignite notes that its proportional dividend adjustment combined
> with split adjustment is "consistent with the Center for Research in Security Prices (CRSP)
> methodology" in the total-return sense. Do not tell a research team that CRSP's `CFACPR`
> price series is dividend-adjusted — it is not.

## 3. Corporate Action Factor Equations

### A. Proportional distribution factor (cash dividends, special dividends, spin-offs)

Given a per-share distributed value $D$ with ex-date $t_{\text{ex}}$ and $P_{\text{cum}}$ the
closing price on the last session **before** the ex-date:

$$f_{\text{dist}} = \frac{P_{\text{cum}} - D}{P_{\text{cum}}} = 1 - \frac{D}{P_{\text{cum}}}$$

Requires $0 < D < P_{\text{cum}}$. A distribution greater than or equal to the cum-date price
yields a non-positive factor and inverts the historical series; the engine rejects it rather
than producing negative prices.

CRSP expresses the spin-off/rights case as $\text{FACPR} = D / P(t_{\text{ex}})$ with adjusted
price $P/(1+\text{FACPR})$. The two forms are algebraically identical when the ex-date price
satisfies $P_{\text{ex}} = P_{\text{cum}} - D$; they diverge only to the extent the actual
ex-date open differs from the theoretical drop, which is one source of genuine cross-vendor
divergence.

### B. Split factor

With $S$ expressed as **new shares per old share** ($S = 2.0$ for a 2-for-1 forward split,
$S = 0.1$ for a 1-for-10 reverse split):

$$f_{\text{split}} = \frac{1}{S} = \frac{\text{Shares}_{\text{old}}}{\text{Shares}_{\text{new}}}$$

CRSP's `FACPR` for splits is the number of *additional* shares per old share, i.e.
$S - 1$, so $\text{CFACPR} = S$ and $P_{\text{adj}} = P / \text{CFACPR}$ — the same result.
In a reverse split CRSP's `FACPR` falls between $-1$ and $0$.

### C. Same-ex-date aggregation

Multiple actions can share an ex-date. "If there are multiple corporate actions on the same EX
date, individual adjustment factors are multiplied to compute the cumulative adjustment
factor." Multiple ordinary cash dividends on one ex-date are instead **summed** into a single
amount before the factor is computed:

$$f_{\text{same-date}} = \left(\prod_j \frac{1}{S_j}\right) \times \left(1 - \frac{\sum_k D_k}{P_{\text{cum}}}\right)$$

Note $1 - (D_1+D_2)/P \neq (1-D_1/P)(1-D_2/P)$; the difference is $D_1 D_2 / P^2$.

### D. Cumulative factors

Two cumulative products are maintained, over **all** applicable actions with an ex-date
strictly after $t$ — not only those whose ex-date coincides with a trading bar:

$$F^{\text{price}}_t = \prod_{i\,:\,t_{\text{ex},i} > t} f_i \qquad
F^{\text{share}}_t = \prod_{i\,:\,t_{\text{ex},i} > t,\ i \in \text{share-changing}} f_i$$

$$P_{\text{adj},t} = P_{\text{raw},t} \times F^{\text{price}}_t \qquad
V_{\text{adj},t} = \frac{V_{\text{raw},t}}{F^{\text{share}}_t}$$

$F^{\text{share}}$ is a strict subset of $F^{\text{price}}$'s factors. Using
$1/F^{\text{price}}$ for volume — a common shortcut — inflates historical volume by
$1/(1-D/P)$ at every cash dividend and corrupts ADV, turnover and participation-rate metrics.

Because the products range over ex-dates rather than bar dates, an ex-date that falls on a
market holiday or in a data gap still adjusts all earlier history. CRSP warns about the
converse case — unknown events inside a trading gap — by setting adjusted values to missing:
"If there is a gap in trading where possible split events are not known, all adjusted values
are set to missing when the gap is between the observation and the adjustment base date."

## 4. Cross-Vendor Reconciliation Tolerance Standards

Comparing two vendor adjusted closes on a shared date:

$$\text{Percentage Difference}_t = \left| \frac{P_{A,t} - P_{B,t}}{(P_{A,t} + P_{B,t})/2} \right| \times 100$$

- **Acceptable alignment**: $\text{Percentage Difference}_t \le$ `tolerance_pct` (default 0.5%).
- **Divergence anomaly**: any date above tolerance triggers an audit flag and fails the report.
- **Uncomparable dates**: a non-finite close, or a non-positive mid price with a non-zero
  difference, is flagged rather than compared. `nan > tolerance` evaluates to `False`, so a
  naive comparison silently reports a clean reconciliation over corrupt data.
- **Coverage**: divergence counts are meaningful only alongside the share of the date union
  actually compared. Agreement on 1 of 300 dates is not a passed reconciliation. Set
  `min_coverage_pct` to fail thin overlaps rather than reading `PASSED` at face value.

The 0.5% default is an engineering starting point for liquid equities, not a regulatory
threshold. Calibrate it per asset class and price level: at a $0.05 quote, one tick of
rounding is already 2%.

## 5. Sources

| Claim | Source | Section | Retrieved |
| :--- | :--- | :--- | :--- |
| `FACPR` = 0 for ordinary cash dividends; = additional shares per old share for splits; = cash amount / ex-date price for spin-offs, rights and non-final liquidating distributions; = −1 for mergers/total liquidations | CRSP, *Data Descriptions Guide*, Chapter 4: Data Definitions — "Factor to Adjust Price" ([mirror](https://leiq.bus.umich.edu/docs/crsp_factor_adjustment.pdf)) | Ch. 4 | 2026-09 |
| "Shares and volumes are only adjusted using stock splits and stock dividends"; $A(t)=P(t)/C(t)$; gap handling sets adjusted values to missing | CRSP, *Data Descriptions Guide*, Chapter 5: CRSP Calculations — "Adjusted Data" ([mirror](https://leiq.bus.umich.edu/docs/crsp_calculations_splits.pdf)) | Ch. 5 | 2026-09 |
| Dividend adjustment factor = (previous close − dividend) / previous close; same-ex-date factors multiplied; same-ex-date ordinary dividends summed; volume adjusted only for share-count changes; `PriceReturn` excludes ordinary cash dividends only | Xignite (a QUODD solution), *Corporate Actions Handling in GlobalHistorical v3* ([PDF](https://quodd.com/hubfs/corporate-actions-handling-in-globalhistorical-v3.pdf)) | Adjustment Principles; Adjustment Methodology | 2026-09 |
| `adjustmentNormal` / `adjustmentAbnormal` / `adjustmentSplit` / `adjustmentFollowDPDF` override axes and the action types each covers | Bloomberg historical data overrides, as documented in the MathWorks Datafeed Toolbox `blp.history` reference ([link](https://www.mathworks.com/help/datafeed/blp.history.html)) | Name-Value Arguments | 2026-09 |

**Unverified — do not assert.** Refinitiv/LSEG's and FactSet's exact per-axis defaults were not
confirmed against primary vendor documentation during this review. The `SPLIT_ONLY_PRICE_RETURN`
methodology is specified here from the Xignite `PriceReturn` definition, not from a Refinitiv
contract. Confirm any specific vendor's defaults against that vendor's own data dictionary and
your entitlement configuration before relying on them.
