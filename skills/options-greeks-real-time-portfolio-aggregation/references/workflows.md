# Workflows for Options Greeks Real-Time Portfolio Aggregation

## 1. Validate every leg before netting anything

- Reject non-finite `delta`, `gamma`, `theta`, `vega`, `position_qty`, `spot_price`
  or `multiplier`; reject a non-positive `spot_price` or `multiplier`; reject a blank
  `symbol` or `underlying_symbol`; reject $|\delta| > 1$.
- Reject the whole book, not just the leg. A partial aggregate is a risk number with
  an unknown amount of the portfolio missing from it — worse than no number, because
  it looks like one.
- **Why this is step one:** `abs(nan) > limit` is `False` and `nan < floor` is
  `False`. One unvalidated NaN Greek turns a breaching portfolio into a
  `PORTFOLIO_GREEKS_HEALTHY` report with `nan` printed in the exposure fields.
- **`multiplier` has no default.** It is deliverable units per contract, taken from
  the contract master. See `references/standards.md` for why 100 is not a safe
  assumption even for US equity options.

## 2. Scale each position to deliverable units and currency

For position $i$ with signed quantity $Q_i$ (long $+$, short $-$), deliverable
$M_i$, spot $S_i$ and per-unit Greeks:

$$\Delta_i = Q_i M_i \delta_i \qquad \Delta_{\text{USD},i} = \Delta_i S_i$$
$$\Gamma_i = Q_i M_i \gamma_i \qquad \Gamma_{\text{USD},i} = \Gamma_i S_i^2 \times 0.01$$
$$\Theta_i = Q_i M_i \theta_i \qquad \nu_i = Q_i M_i \nu^{\text{unit}}_i$$

- The sign lives on $Q_i$. Per-unit Greeks stay exactly as the vendor quoted them; a
  short call keeps its positive per-unit delta and the position delta comes out
  negative.
- $\Theta_i$ is one **calendar** day of decay. $\nu_i$ is the P&L of a **1 percentage
  point** move in that position's implied volatility.
- $\Gamma_{\text{USD},i}$ is the dollar delta gained on a $+1\%$ move: one factor of
  $S$ sizes the move in dollars ($0.01 S$), the other converts the delta gained (in
  deliverable units) into currency.

## 3. Net the portfolio and group by underlying

- Sum with `math.fsum`, not repeated `+=`. Limit checks are threshold comparisons; a
  large book netting close to a limit must not have its status decided by the order
  the positions arrived in.
- Additive across underlyings: Dollar Delta, Dollar Gamma, Theta, Vega.
- **Not** additive across underlyings: raw delta units and raw gamma. Both are
  per-underlying quantities. Report them, flag `is_single_underlying`, and direct
  multi-name consumers to the currency figures or the `by_underlying` breakdown.
- Group on the case-normalised underlying symbol so `aapl` and `AAPL` do not become
  two books.

## 4. Audit every limit independently

| Limit | Test |
|---|---|
| `max_dollar_delta_usd` (magnitude, $>0$) | $\|\Delta_{\text{USD,net}}\| > L$ |
| `max_negative_theta_usd` (signed floor, $\le 0$) | $\Theta_{\text{net}} < L$ |
| `max_vega_usd` (magnitude, $>0$) | $\|\nu_{\text{net}}\| > L$ |
| `max_abs_dollar_gamma_usd` (magnitude, optional) | $\|\Gamma_{\text{USD,net}}\| > L$ |

- Evaluate all four. Return a `breaches` list plus per-limit booleans; let `status`
  name only the highest-precedence breach (dollar delta → theta → vega → dollar
  gamma). A first-match-wins status tells the operator the other limits are clean
  when they are not.
- Compare against the **rounded** values that are reported, so the printed number and
  the status can never disagree.
- The theta limit floors decay only. A short-premium book collecting theta is not a
  breach, and a positive floor would flag every portfolio — reject it at construction.

## 5. Emit the report

`PortfolioGreeksReport` carries the nets, `net_dollar_gamma_usd`, the `by_underlying`
breakdown, `breaches`, the `is_*_breached` flags, `underlying_count`,
`is_single_underlying`, and a human-readable audit line naming every breach.

- Log at `warning` when anything is breached, `info` otherwise.
- Route a breach to whatever acts on it. This engine observes; it does not hedge
  (`greeks-based-portfolio-hedging-automation`) and does not halt trading
  (`kill-switch-and-drawdown-circuit-breakers`).
