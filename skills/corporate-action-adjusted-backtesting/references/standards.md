# Standards for Corporate Action Adjusted Backtesting

## 0. How to read this document

Section 1 records the **industry adjustment convention** this skill implements, with the
published sources that define it. Sections 2–3 are **engineering standards** — this
repository's recommended practice, labelled as such so an agent does not present them to
an operator as an external mandate.

Nothing here is a regulatory requirement. Corporate action adjustment is a data
methodology, not a compliance regime; no regulator prescribes an adjustment formula.
Where a backtest is retained as evidence of a firm's testing obligations (for example
MiFID II RTS 6 Art. 5), what matters is that the methodology is documented and
reproducible — not that it matches any particular vendor.

## 1. The CRSP adjustment convention

The dominant convention for backward-adjusted equity price series. Two independent
vendor implementations document it in the same terms and both attribute it to CRSP:

| Source | What it states | URL |
|---|---|---|
| Yahoo Finance, "What is the adjusted close?" | Pre-dividend data is multiplied by `(1 - dividend amount / closing price)`, where the closing price is the one **before** the ex-dividend date. Pre-split data is multiplied by the split ratio (0.5 for a 2-for-1). States the data is adjusted "adhering to Center for Research in Security Prices (CRSP) standards". | <https://help.yahoo.com/kb/SLN28256.html> |
| MathWorks, `adjustedClosingPrices` (Financial Toolbox) | "adjusts closing prices for cash dividends by scaling them by a dividend multiplier in which it expresses the dividend as a fraction of **the last closing price preceding the ex-dividend date**"; splits are handled by dividing by the split ratio. States the approach "adheres to the Center for Research in Security Prices (CRSP) standard". | <https://www.mathworks.com/help/finance/adjustedclosingprices.html> |
| CRSP, Data Definitions — F (factor to adjust shares outstanding) | `FACSHR` adjusts shares observations for stock splits, stock dividends and other distributions **that change the share count**. Ordinary cash dividends do not. | <http://www.crsp.com/products/documentation/data-definitions-f> |

Worked example, from the Yahoo page: a $0.08 dividend with a $24.96 close on the day
before the ex-date gives a multiplier of `1 - 0.08/24.96 = 0.9968`.

**Not verified here:** the exact text of CRSP's own `FACPR` variable definition, which is
behind CRSP's documentation. The formula above is stated as the convention that Yahoo
and MathWorks implement and attribute to CRSP, which is the claim this skill relies on.
Do not restate it as a quotation from CRSP's own definitions.

## 2. Engineering standards — factors

| Standard | Requirement |
|---|---|
| Dividend reference price | The dividend factor MUST be `1 - D / P_close(last bar strictly before the ex-date)`. The ex-date close MUST NOT be used: it couples the factor to that session's market move and diverges without bound as the ex-date close approaches zero. |
| Price vs. share-count separation | Two factors MUST be carried. The price factor includes splits, reverse splits and cash dividends; the share-count factor includes **share-count events only**. Volume MUST be adjusted by the share-count factor (`V_adj = V_raw / volume_caf`), never by the price factor — a cash dividend changes no share count. |
| CAF anchoring | The cumulative factor MUST be `1.0` on the most recent bar in the series, so the newest adjusted price equals the newest raw price. Events with an ex-date after the last bar MUST NOT be applied, since doing so breaks that anchor. |
| Date-keyed application | An event's factor MUST apply to every bar with `dt < ex_date`, keyed by date. It MUST NOT require a bar on the ex-date itself — ex-dates fall on holidays, halted sessions and calendar gaps. |
| Factor precision | Cumulative factors MUST NOT be rounded before use. A long split history drives the factor below `1e-6`, where rounding to six places is total precision loss. Rounding belongs to presentation only. |

## 3. Engineering standards — usage

| Standard | Requirement |
|---|---|
| Signal vs. execution separation | Signals, indicators and return series MUST be computed on adjusted prices. Order quantity, cash debit/credit, commission and tick rounding MUST use raw unadjusted prices. |
| Dividend cash | Dividend PnL MUST be credited explicitly from the event log and the raw position held on the ex-date (`cash += shares * D`). It MUST NOT be inferred from the adjusted price series — the price factor removes the drop, it does not pay the cash. |
| Point-in-time vantage | A backtest that walks forward MUST restrict the event set to ex-dates on or before the simulated date. A fully adjusted modern series encodes future events into today's price. |
| Fail loud | Unrecognised event types, non-positive split ratios, non-finite values, and dividends at or above their reference close MUST raise rather than default to a factor of 1.0. A silently skipped event leaves an uncorrected gap in a series the caller believes is adjusted. |
