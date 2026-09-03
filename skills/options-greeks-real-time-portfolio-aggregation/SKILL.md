---
name: options-greeks-real-time-portfolio-aggregation
description: >-
  Use when a multi-leg options book needs one continuously refreshed exposure figure,
  netting per-position delta, gamma, theta and vega into dollar terms using each
  contract's deliverable. Acting on it is greeks-based-portfolio-hedging-automation.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: multi-asset-derivatives
  tags: risk-management, options-greeks, portfolio-aggregation, dollar-delta, dollar-gamma, theta-decay, vega-exposure
  brokers_frameworks: "Portfolio Greeks Aggregation Engine; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when running an options or multi-leg derivatives book and you need one continuously refreshed answer to "what is this portfolio actually exposed to right now?" Options payoffs are nonlinear, so a book that looks flat in contract counts can carry millions of dollars of directional exposure, thousands of dollars a day of decay, and a vega position that only shows up when implied volatility moves. This engine scales each position's per-unit Greeks by its own deliverable, nets them into portfolio Dollar Delta ($\Delta_{\text{USD}} = Q M \Delta S$), Dollar Gamma, daily Theta and Vega, breaks the result down by underlying, and audits **every** limit independently.

## When NOT to Use

- **As a pricing or Greeks engine.** Greeks are inputs. The engine cannot tell a stale delta from a live one, or a bad vega from a good one, and it will confidently net a mismarked position into the total. Build the surface first — `options-implied-volatility-surface-construction`.
- **As a hedger.** The report is an observation, not an order. Sizing and sequencing the offsetting trade is a separate job — `greeks-based-portfolio-hedging-automation`.
- **As a kill switch or drawdown control.** A limit breach here is a signal to a human or to a separate control; it stops nothing on its own. Circuit breakers must be independent — `kill-switch-and-drawdown-circuit-breakers`.
- **Across currencies without converting first.** Every `_usd` figure is in whatever currency the inputs were quoted in. Netting USD-quoted SPX Greeks with BTC-quoted crypto option Greeks produces a number with no unit — see `multi-currency-pnl-and-fx-conversion`.
- **Near expiry on at-the-money strikes.** Delta is discontinuous through the pin, so a snapshot of it is unstable no matter how often you refresh — see `options-pin-risk-management-at-expiry`.
- **As a substitute for revaluation.** The Greeks are a first-order snapshot at one spot/vol point. Aggregating faster does not make them fresher — `real-time-greeks-recalculation-on-market-moves`.

## Prerequisites

- Per-position Greeks **per unit of the deliverable** (`delta`, `gamma`, `theta`, `vega`), never per contract.
- Signed quantities: long $+Q$, short $-Q$, with the per-unit Greeks left as quoted.
- **`multiplier` per position — deliverable units per contract, read from the contract master.** Required, no default. 100 for a standard, *unadjusted* US equity option; different for OCC-adjusted contracts, index products and crypto.
- `spot_price` per position, in the same currency as that position's Greeks.
- Limits: `max_dollar_delta_usd` (magnitude, $>0$), `max_negative_theta_usd` (signed floor, $\le 0$), `max_vega_usd` (magnitude, $>0$), and optionally `max_abs_dollar_gamma_usd`.

## Workflow

1. **Validate before you net.**
   - Reject any position with a non-finite Greek, a non-positive `multiplier` or `spot_price`, a blank symbol, or $|\delta| > 1$.
   - **Decision point — a NaN is not a small error, it is an invisible one.** `abs(nan) > limit` evaluates to `False`, so a single NaN delta netted into the total produces a portfolio reporting `PORTFOLIO_GREEKS_HEALTHY` with `nan` exposure. Raise on the bad leg; never aggregate a book that contains one.
   - **Decision point — $|\delta| > 1$ means the feed quoted delta in percent.** A delta of `60` instead of `0.60` overstates exposure $100\times$. Reject it rather than scaling it.

2. **Scale each position by its own deliverable.**
   $$\Delta_i = Q_i M_i \delta_i, \quad \Delta_{\text{USD},i} = \Delta_i S_i, \quad \Gamma_i = Q_i M_i \gamma_i$$
   $$\Gamma_{\text{USD},i} = \Gamma_i S_i^2 \times 0.01, \quad \Theta_i = Q_i M_i \theta_i, \quad \nu_i = Q_i M_i \nu^{\text{unit}}_i$$
   - **Decision point — $M_i$ is the *deliverable*, not the premium multiplier.** After a corporate action the OCC holds the premium multiplier at 100 and changes the deliverable instead: a 1-for-20 reverse split leaves a contract delivering **5 shares**. Scaling that position by 100 overstates its risk exactly $20\times$, with no error anywhere to catch it.

3. **Net the portfolio, and know which totals are additive.**
   - Currency figures — Dollar Delta, Dollar Gamma, Theta, Vega — are additive across underlyings.
   - **Decision point — raw Delta (units) and raw Gamma are not.** Adding a \$500 name's delta units to a \$5 name's is dimensionally meaningless. Report them only for a single-underlying book (`is_single_underlying`), and use Dollar Delta / Dollar Gamma or the `by_underlying` breakdown otherwise.
   - Dollar Gamma, $\Gamma S^2 \times 0.01$, is the cross-asset normalisation for gamma: the dollar delta the book picks up on a $+1\%$ move. One factor of $S$ sizes the move in dollars, the other converts the delta gained into currency.
   - Sum with `math.fsum`, not `+=`: a large book that nets close to a limit must not have its breach status decided by position ordering.

4. **Audit every limit independently.**
   - $|\Delta_{\text{USD,net}}| > \text{max\_dollar\_delta\_usd}$, $\;\Theta_{\text{net}} < \text{max\_negative\_theta\_usd}$, $\;|\nu_{\text{net}}| > \text{max\_vega\_usd}$, and optionally $|\Gamma_{\text{USD,net}}| > \text{max\_abs\_dollar\_gamma\_usd}$.
   - **Decision point — the theta limit is a signed floor, not an absolute value.** Only *decay* is capped. A short-premium book collecting \$50,000/day is not a theta breach, and testing $|\Theta| \le \text{limit}$ against a negative limit flags every book forever.
   - **Decision point — never stop at the first breach.** An `if/elif` chain reports one status and leaves the operator believing the other limits are clean. Evaluate all four, return `breaches` and the `is_*_breached` flags, and let `status` carry only the highest-precedence one.

5. **Emit the report.** `PortfolioGreeksReport` carries the nets, the per-underlying breakdown, every breach flag, and an audit line. Compare limits against the same rounded values that are reported, so the status can never contradict the number printed beside it.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Omitting the contract multiplier**: computing dollar delta as $Q \times \delta \times S$ treats a contract count as a share count and understates a standard equity option's exposure $100\times$. The book is effectively unmonitored while the report claims a small number.
- **Hard-coding the multiplier at 100**: the OCC keeps the *premium* multiplier at 100 through corporate actions but adjusts the *deliverable* — 5 shares after a 1-for-20 reverse split, a basket after a merger. Greeks scale with the deliverable. A Deribit BTC option is 1 BTC per contract, not 100.
- **Reporting only the first breach**: a book can be over its delta, theta and vega limits simultaneously. A single `status` string derived from an `if/elif` chain hides the other two, and the operator reads the silence as compliance.
- **Testing $|\Theta|$ against a negative limit**: `abs(theta) <= -5000` is never true. Written that way the check fires on every portfolio, gets muted as noise, and then the real decay breach is invisible too.
- **Letting a NaN Greek into the total**: `abs(nan) > limit` is `False`, so one corrupt leg turns a breaching book into a healthy-looking one. This is strictly worse than a crash — reject the leg.
- **Summing raw delta or gamma across underlyings**: the fix for delta is Dollar Delta ($\Delta \times S$); the fix for gamma is Dollar Gamma ($\Gamma S^2 \times 0.01$). Without it, a large gamma on a cheap stock and a small gamma on an expensive one look interchangeable when their economics differ by orders of magnitude.
- **Reading daily theta as a P&L forecast over a weekend**: theta is per *calendar* day, and pricing models decay seven days over a five-day trading week. Friday's reading understates the decay realised by Monday's open roughly threefold.
- **Treating net vega as a diversified number**: a single net vega assumes every implied vol in the book moves one point *together*. A book long vol in one name and short vol in another nets to something small that will not behave that way in a real vol shock.
- **Comparing limits against unrounded values while reporting rounded ones**: a total that prints as exactly the limit while the status says `BREACH` destroys trust in the report and wastes the operator's time reconciling it.
- **Treating the aggregate as fresh because it was computed quickly**: the number is only as current as the Greeks fed into it. A fast aggregation of a stale surface is a stale risk number delivered promptly.

## Verification

- **Baseline scaling**: 10 long calls, $M=100$, $S=\$100$, $\delta=0.50$, $\gamma=0.02$, $\theta=-0.05$, $\nu=0.10$ $\implies$ `net_delta_shares` $=+500$, `net_dollar_delta_usd` $=\$50{,}000$, `net_gamma` $=20$, `net_dollar_gamma_usd` $=\$2{,}000$, `net_theta_daily_usd` $=-\$50$, `net_vega_usd` $=\$100$.
- **Sign convention**: long 10 calls ($\delta=0.60$) plus short 5 puts ($\delta=-0.40$) on the same \$150 underlying $\implies$ `net_delta_shares` $=+800$, `net_dollar_delta_usd` $=\$120{,}000$, `net_dollar_gamma_usd` $=\$3{,}375$.
- **Adjusted contract**: the same 10 contracts against a deliverable of 5 at $S=\$300$ $\implies$ \$9,000 of dollar delta, not the \$180,000 a hard-coded 100 would produce.
- **Dollar Gamma normalisation**: 1 contract at $S=\$500,\ \gamma=0.001$ and 1 contract at $S=\$50,\ \gamma=0.1$ both give `dollar_gamma_usd` $=\$250$, despite raw gamma differing $100\times$.
- **Multiple breaches**: 100 contracts, $M=100$, $S=\$500$, $\delta=0.80$, $\theta=-1.00$, $\nu=2.00$ against the default limits $\implies$ `breaches` $=$ `[DOLLAR_DELTA_BREACH, THETA_LIMIT_BREACH, VEGA_LIMIT_BREACH]`, `status` $=$ `DOLLAR_DELTA_BREACH`, and all three `is_*_breached` flags `True`.
- **Theta floor semantics**: a book collecting $+\$50{,}000$/day of theta is `PORTFOLIO_GREEKS_HEALTHY`; $-\$5{,}000.00$/day is exactly at the floor and clean; $-\$5{,}000.01$/day breaches.
- **Boundary**: dollar delta of exactly \$500,000 against a \$500,000 limit is not a breach; \$500,010 is.
- **Negative checks**: a NaN/Inf Greek, a delta of `60`, a non-positive `multiplier` or `spot_price`, a blank symbol, and a limit set to zero, negative, or a positive theta floor must each raise.
- Run `python -m unittest discover -s skills/options-greeks-real-time-portfolio-aggregation/scripts` and confirm a 100% pass rate.

## Related Skills

- `greeks-based-portfolio-hedging-automation`
- `real-time-greeks-recalculation-on-market-moves`
- `options-implied-volatility-surface-construction`
- `options-pin-risk-management-at-expiry`
- `options-chain-data-normalization-across-vendors`
- `options-backtesting-with-realistic-iv-surface`
- `multi-currency-pnl-and-fx-conversion`
- `kill-switch-and-drawdown-circuit-breakers`
