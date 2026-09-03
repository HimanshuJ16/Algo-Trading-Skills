# Deep Workflow Reference — value-at-risk-var-live-monitoring

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

### 1. Configure the monitor once, at startup

```python
from var_monitor import LiveValueAtRiskMonitor

monitor = LiveValueAtRiskMonitor(
    confidence_level=0.99,     # (0.5, 1.0); exact quantile, any level
    var_limit_pct=0.05,        # fraction of NAV; breach is >= limit
    cvar_limit_pct=None,       # set to bring Expected Shortfall into the verdict
    min_observations=0,        # 0 -> ceil(1/(1-c)) = 100 at 99%
    subtract_mean_drift=True,  # z*sigma - mu; False for z*sigma
)
```

Every invalid configuration is rejected here rather than at the first risk cycle:
`confidence_level` outside $(0.5, 1)$, a non-positive or non-finite limit, a negative
`min_observations`. A `confidence_level` of `1.5` is not a harmless typo — under a
`int((1-c)·n)` index it produces a *negative* index that reads the profit tail and
reports zero historical VaR on a loss-making book.

### 2. Assemble aligned inputs each risk cycle

- `positions`: `symbol -> signed quantity`. **Shorts are negative quantities.** A
  negative *price* is rejected: it would flip the sign of the weight and invert the
  position's contribution to risk.
- `prices`: `symbol -> current price` in the NAV currency, strictly positive and finite.
- `returns_dict`: `symbol -> return history`, **all series the same length, indexed to
  the same observation dates, oldest first**, ending at the last *completed* period.
- `portfolio_nav`: strictly positive. At or below zero equity, VaR-as-a-fraction-of-NAV
  is undefined — escalate to the margin/liquidation path instead.

Alignment is upstream work. Do it by joining on the observation date and trimming to the
common window (`series[-n:]` for each symbol only if you already know they end on the
same date). The module rejects ragged input; it cannot detect *mis-dated* input.

### 3. Value the book and derive signed weights

$V_i = q_i \cdot p_i$, $w_i = V_i / \text{NAV}$.

Because weights are signed, `gross_exposure_pct` $= \sum_i |w_i|$ and
`net_exposure_pct` $= \sum_i w_i$ fall out for free. A market-neutral book has net ≈ 0
and gross ≫ 0; a 3x-levered long book has gross ≈ 3.0 and roughly 3x the VaR of the
unlevered version. Leverage needs no separate scaling factor — it is already in $w$.

### 4. Reconstruct the portfolio return series

$R_{p,t} = \sum_i w_i R_{i,t}$ over the aligned window, at current weights. This is a
*constant-weight* reconstruction: it asks "what would today's book have returned on each
past day", which is the intended question for a live limit and is **not** the book's
realised P&L history.

### 5. Parametric (variance-covariance) VaR

$\text{VaR}_{\text{param}} = z_c \sigma_p - \mu_p$, clamped at 0.

$z_c$ comes from `statistics.NormalDist.inv_cdf` — exact at every confidence level and
free of third-party dependencies. Do not reintroduce a lookup table: a fallback default
silently returns the wrong multiplier, and the failure direction that matters is the one
reporting *less* risk than exists (99% z used for a 99.9% monitor understates by 25%).

### 6. Historical Simulation VaR and CVaR

Sort worst-first. With $k = \lceil n(1-c) \rceil$ (clamped to $[1, n]$):

- $\text{VaR}_{\text{hist}}$ = loss of the $k$-th worst observation.
- $\text{CVaR}$ = mean loss of the $k$ worst observations.

The $\lceil \cdot \rceil$ needs an epsilon: `1 - 0.99` is `0.010000000000000009` in
binary floating point, so a bare `ceil(100 * (1 - 0.99))` returns 2, not 1, and shifts
the quantile at precisely the round sample sizes the convention exists to pin down.

`tail_observations_used` reports $k$. When $k = 1$, CVaR is numerically identical to VaR
by definition — that is the estimate telling you the sample is too thin to say anything
about tail *severity*, not a bug.

### 7. Breach evaluation and attribution

Each measure breaches at $\ge$ its limit. Record:

- `breaching_measures` — which of `parametric_var`, `historical_var`, `cvar` tripped.
- `binding_var_pct` — the largest breaching value.

A single approve/deny bit is not enough for a Tier-5 control. A breach message that
quotes the parametric figure when the historical measure tripped is an audit record that
contradicts itself, and the operator reading it at 09:31 cannot tell which model to act
on.

### 8. Enforce — vetoing risk-*increasing* orders only

```python
status = monitor.evaluate_live_risk(
    positions, prices, returns_dict, nav,
    is_risk_reducing=order.reduces_exposure,   # close / trim / hedge
)
if not status.approved:
    reject(order, status.breach_reason)
```

`is_risk_reducing=True` approves through a live breach, with `breach_reason` still
populated and `risk_reducing_override=True` for the audit trail. The module cannot
verify the assertion — deciding whether an order genuinely reduces exposure is the
caller's job, and getting it wrong turns the override into a bypass.

The verdict describes the **current** book. To gate on the post-fill state, fold the
prospective fill into `positions` and evaluate that instead.

## Known Failure Modes

- **Ragged history front-truncated.** `n = min(len(...))` then indexing `0..n-1` pairs
  the oldest observations of a long series with recent observations of a short one. A
  50/50 book of one asset at +2% and one at −2% every day has a true VaR of zero; on a
  220/120 length split this reported 1.69% parametric VaR with no warning. Fail closed.
- **Silent z-table fallback.** `z_table.get(level, 2.326)` gave a 99.9% monitor the 99%
  multiplier — a 25% understatement of the limit-relevant number.
- **NaN disabling the breaker.** `NaN >= limit` is `False`. A single bad tick can make a
  risk control report success while approving every order. Reject non-finite input
  before it reaches the comparison.
- **Wrong exception type escaping.** A held symbol missing from `returns_dict` raised a
  bare `KeyError`, and a NaN return raised `AttributeError` from `statistics` internals.
  Both slip straight past a caller's `except VaRMonitorError` guard.
- **99% VaR from 10 bars.** With `int((1-c)·n)` as a 0-based index and $n < 100$, the
  "99% historical VaR" is the single worst observation and CVaR equals it exactly.
- **Blocking the exit.** A blanket veto on breach refuses the closing and hedging trades
  that would cure the breach, turning a limit excursion into a trapped position.
- **Unpriced positions silently dropped.** Filtering held symbols on `s in prices`
  quietly removes real exposure from the risk number.

## Production Implementation Reference

- Reference code: `scripts/var_monitor.py`
  (`LiveValueAtRiskMonitor`, `VaRMetrics`, `LiveRiskStatus`, `VaRMonitorError`).
- Automated unit tests: `scripts/test_var_monitor.py` — 31 tests including hand-derived
  estimator values and a regression test per defect listed above.
- Run with:
  `python -m unittest discover -s skills/value-at-risk-var-live-monitoring/scripts`
