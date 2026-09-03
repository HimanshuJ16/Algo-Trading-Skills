---
name: leverage-limit-enforcement-across-instruments
description: >-
  Use when one account holds equities, perpetuals, FX, futures and options and
  per-symbol size limits no longer describe how levered it is; projects post-fill gross,
  net-directional and per-asset-class leverage and vetoes the order.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: risk-management
  tags: risk-management, leverage-limit, gross-leverage, net-leverage, pre-trade-risk, cross-asset-exposure
  brokers_frameworks: "Reg T (12 CFR 220); FINRA Rule 4210; SEC Rule 15c3-5; AIFMD Delegated Regulation (EU) 231/2013; Python Standard Library"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when one account or fund holds positions across heterogeneous asset classes — cash equities, crypto perpetuals, FX, futures, options — and a single per-symbol size limit no longer describes how levered the book is. The gate projects what the book would look like *if* the proposed order filled, and measures three ratios against configured caps:

$$L_{\text{gross}} = \frac{\sum_i |E_i|}{\text{Equity}} \qquad L_{\text{net}} = \frac{\left|\sum_i E_i\right|}{\text{Equity}} \qquad L_{\text{class}(c)} = \frac{\sum_{i \in c} |E_i|}{\text{Equity}}$$

where $E_i$ is the signed underlying-equivalent exposure of instrument $i$ — netted per instrument, summed gross across instruments. By the triangle inequality $L_{\text{net}} \le L_{\text{gross}}$ always, so a `max_net_leverage` set at or above `max_gross_leverage` is a gate that can never fire.

The shape follows the AIFMD gross method (Delegated Regulation (EU) No 231/2013, Art. 7: "the sum of the absolute values of all positions", derivatives converted to an equivalent underlying position per Annex II). Automated pre-trade rejection against pre-set thresholds is what SEC Rule 15c3-5(c)(1)(i) requires of US broker-dealers with market access.

## When NOT to Use

- **As a margin or liquidation control.** This measures exposure against equity. It knows nothing about the broker's initial or maintenance requirement, SPAN, portfolio margin, or cross-margin offsets, so a book comfortably inside 3.0× gross can still be one tick from a margin call. Pair it with `margin-utilization-circuit-breaker` and `broker-account-margin-call-handling`.
- **As the only pre-trade control.** Leverage caps bound size, not concentration, correlation, drawdown, or per-symbol risk. Rule 15c3-5 expects a control *suite*; compose with the skills under Related Skills.
- **On a hedge-mode book where you want both legs counted separately.** Netting is per symbol, so a simultaneous long and short in the same contract (Binance/Bybit hedge mode, some MT5 accounts) collapses to its net delta. That is the correct *leverage* answer but understates margin consumption; if you need both legs gross, model them as distinct symbols and accept that closing orders then need `reduce_only` handling at the caller.
- **With stale or mismatched marks.** The ratios are exactly as fresh as the `notional_usd` values handed in. Feeding yesterday's marks during a gap produces a confidently wrong approval.
- **As a portfolio-level risk model.** Gross leverage treats a Treasury future and a small-cap crypto perp as the same dollar of exposure. It is a blunt size cap, deliberately — use volatility- or VaR-based sizing for risk equivalence.
- **Under concurrency, without caller-side serialization.** The engine is stateless and thread-safe, but two orders evaluated against the same snapshot can each pass and jointly breach.

## Prerequisites

- `portfolio_equity_usd` — strictly positive, finite account equity / NAV in the reporting currency. Convert a multi-currency book first (see `multi-currency-pnl-and-fx-conversion`).
- `current_positions` — `List[PositionSpec]` with `symbol`, `asset_class`, `side` (strictly `BUY`/`SELL`), non-negative `notional_usd`, and `exposure_delta`.
- `proposed_order` — a `ProposedOrderSpec` with the same fields.
- Limit configuration: `max_gross_leverage`, `max_net_leverage`, `asset_class_limits`, and — only if you want unconfigured classes to trade — an explicit `default_asset_class_limit`.

For options and other non-linear instruments, `notional_usd` is the **underlying** notional (contracts × contract size × underlying price) and `exposure_delta` is the option delta. This is the Annex II conversion: `number of contracts × notional contract size × market value of the underlying × delta`.

## Workflow

1. **Normalise the book into signed underlying-equivalent exposures**:
   - $E_i = (+1 \text{ if BUY else } -1) \times \text{notional}_i \times \delta_i$, keyed by normalised symbol.
   - **Decision point — validate, never coerce.** A `side` of `"LONG"` is rejected, not guessed. Silently mapping an unrecognised side to *short* inverts the net measure: a 2.1× long book reads as 0.1× and walks through the net gate.
   - **Decision point — one symbol is one position.** Rows sharing a symbol are netted before absolute values are taken. Skipping this is what makes a naive gate treat a closing order as a second opposing leg.
   - A symbol appearing under two asset classes raises: netting and class caps cannot both be resolved.

2. **Compute current and projected exposures**:
   - Apply the order to the same keyed map — netting against an existing position in that symbol, or opening a new key.
   - $\text{Gross} = \sum |E_i|$, $\text{Net} = |\sum E_i|$, and per class $\sum_{i \in c} |E_i|$. Longs and shorts in *different* instruments never net into gross: that offset assumes a correlation hedge that fails in exactly the stress event the cap exists for.

3. **Resolve the asset-class cap — fail closed**:
   - **Decision point — an unconfigured asset class is rejected** (`REJECTED_UNKNOWN_ASSET_CLASS`), not waved through on a hidden default. Set `default_asset_class_limit` explicitly if a fallback is genuinely wanted; a limit nobody chose is not a control.
   - An unenforceable cap is treated as a *failed* cap, not an absent one, so it flows into step 5: opening exposure in that class is rejected, while orders that reduce it are still permitted. Exposure already on the book must never be trapped by a config gap.

4. **Evaluate the caps on unrounded ratios**:
   - Compare $L_{\text{projected}} \le \text{limit}$ (inclusive) on the raw quotient, with only a $10^{-9}$ relative tolerance for float representation.
   - **Decision point — never round before comparing.** Rounding to 2 dp first admits breaches up to $0.005\times$ NAV and then reports the book as sitting exactly *on* the cap. Round for the report, never for the gate.

5. **Classify the order before vetoing**:
   - **Decision point — de-risking is never blocked.** If the order raises none of the three ratios *and* strictly lowers every ratio that is currently breached, approve it as `APPROVED_RISK_REDUCING_WHILE_OVER_LIMIT` even though the book is over a cap. A gate that blocks the only orders capable of curing a breach traps the desk in it.
   - The "strictly lowers" half matters: reversing a position to the same size leaves every ratio unchanged. That is a large new trade, not remediation, and is vetoed.
   - Otherwise veto in precedence order: gross → net → asset class.

6. **Emit `LeverageEnforcementReport`**: current and projected ratios at full precision, per-limit pass flags, every asset class's projected leverage, the risk-reducing flag, and an audit note. Breaches in classes the order does not touch are logged, not vetoed — the order did not cause them.

> Full procedure: see `references/workflows.md`.
> Standards and jurisdictional anchoring for every default: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Vetoing de-risking orders**: appending the proposed order as a new leg instead of netting it against the existing position turns a $\$100\text{k}$ sell against a $\$300\text{k}$ long into a projected $4.0\times$ instead of $2.0\times$. The book is then frozen at its worst moment — every unwind is rejected as an exposure increase.
- **Rounding the ratio before the comparison**: `round(L, 2) <= 3.0` approves a genuine $3.00499\times$ book and records it as $3.00\times$. On a $\$1\text{bn}$ NAV that is $\$5\text{m}$ of unauthorised notional with a clean audit trail behind it.
- **Guessing at an unrecognised `side`**: `"LONG"`/`"SHORT"`/`"BUY_TO_OPEN"` treated as anything other than an error flips exposure signs. The gross measure is unaffected, so the corruption is invisible until the net gate fails to fire.
- **Confusing gross and net leverage**: a $5.0\times$ gross book with $0.0\times$ net is not flat. The offset is two positions that both have to be liquidated, at a spread that widens exactly when correlations converge.
- **Ignoring derivative synthetic leverage**: counting an option at the premium paid rather than delta-adjusted underlying notional understates exposure by an order of magnitude — a $\$5\text{k}$ premium can carry $\$40\text{k}$ of underlying-equivalent exposure.
- **Applying uniform caps across asset classes**: one $10\times$ cap spanning EUR/USD and a small-cap perpetual prices a two-sigma day in each as if it were the same event.
- **Silent fallback limits**: `limits.get(asset_class, 2.0)` gives every instrument type nobody configured a cap nobody chose. Fail closed instead.
- **Treating the cap as a margin control**: passing a $3.0\times$ gross check says nothing about maintenance margin. FINRA Rule 4210(c) permits $4{:}1$ on equities at maintenance level and portfolio margin permits more — the leverage cap and the liquidation threshold are different numbers with different owners.
- **Check-then-trade races**: the gate is stateless, so two orders evaluated against the same position snapshot can each pass and jointly breach. Serialize check-then-place at the caller.
- **Treating the net measure as the AIFMD commitment method**: $|\sum E_i|$ nets everything against everything. The commitment method (Art. 8) nets only duly-verified hedging and netting arrangements. Do not report one as the other.

## Verification

- Instantiate `LeverageLimitEnforcerEngine(max_gross_leverage=5.0, max_net_leverage=3.0, asset_class_limits={"EQUITY": 2.0, "CRYPTO": 3.0, "FX": 10.0})`. On $\$100\text{k}$ equity with Long $\$100\text{k}$ AAPL and Short $\$50\text{k}$ TSLA ($L_{\text{gross}} = 1.5\times$, $L_{\text{net}} = 0.5\times$), a $\$50\text{k}$ MSFT buy projects $2.0\times$ gross / $1.0\times$ net $\Rightarrow$ `ORDER_LEVERAGE_APPROVED`; a $\$350\text{k}$ BTC-PERP buy projects $5.5\times \Rightarrow$ `REJECTED_GROSS_LEVERAGE_BREACH`.
- Netting: against a $\$300\text{k}$ AAPL long on $\$100\text{k}$ equity, selling $\$100\text{k}$ of AAPL must project $2.0\times$ — not $4.0\times$ — and be approved.
- Risk-reducing: with the book at $4.0\times$ against a $3.0\times$ cap, a $\$50\text{k}$ sell must return `APPROVED_RISK_REDUCING_WHILE_OVER_LIMIT`, while an $\$800\text{k}$ sell (same-size reversal, $4.0\times \to 4.0\times$) must be vetoed.
- Precision: a projected $3.00499\times$ against a $3.0\times$ cap must be rejected and reported as $3.00499$; an exactly $3.0\times$ projection must be approved.
- Negative checks: `side="LONG"`, a negative or non-finite notional, a NaN or non-positive equity, a non-`PositionSpec` row, and a symbol declared under two asset classes must each raise `ValueError`. An *opening* order in an asset class with no configured cap must return `REJECTED_UNKNOWN_ASSET_CLASS`, while an order *reducing* that class must still be approved.
- Delta: a long put on $\$100\text{k}$ of underlying at $\delta = -0.40$ alongside a $\$100\text{k}$ long must project $1.4\times$ gross and $0.6\times$ net.
- Run `python -m unittest discover -s skills/leverage-limit-enforcement-across-instruments/scripts` and confirm 100% pass rate.

## Related Skills

- `margin-utilization-circuit-breaker`
- `broker-account-margin-call-handling`
- `correlation-aware-exposure-limits`
- `portfolio-level-stop-loss-independent-of-strategy-stops`
- `kill-switch-and-drawdown-circuit-breakers`
