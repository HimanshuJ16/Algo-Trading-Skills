# Workflows for Multi-Day Execution Schedules

## 1. Establish the inputs before scheduling anything

| Input | What to check |
|---|---|
| `total_parent_quantity` | In **shares**, not notional. A quantity supplied as currency inflates the horizon by roughly the price per share; the engine raises past `MAX_HORIZON_SESSIONS` rather than trying to build the schedule. |
| `adv_shares` | Record the measurement window with the order. If a statutory cap applies, the window MUST be the one the rule names (10b-18: four calendar weeks; EU 2016/1052: 20 trading days or the preceding month). |
| `max_daily_participation_pct` | A house limit unless a programme-specific rule applies. See `references/standards.md`. |
| `volatility_daily_pct` | Same frequency as ADV — daily. It drives both the impact estimate and the overnight risk; an annualised figure inflates both by roughly $\sqrt{252}$. |
| `shares_outstanding` | Needed for the permanent-impact term. Omit it and permanent impact is reported as `None`, not as zero. |

## 2. Determine the horizon

1. Daily ceiling: $\text{Cap} = \text{ADV} \times p_{\max}$.
2. Minimum feasible horizon: $N_{\min} = \lceil Q / \text{Cap} \rceil$, computed in share
   units so a quantity that is an exact multiple of the cap does not gain a spurious extra
   session from floating-point division.
3. **Decision point — the minimum horizon is a constraint, not a recommendation.** At
   $N_{\min}$ the order consumes essentially all available capacity, so the cap fixes every
   slice and all three profiles collapse to the same flat schedule. Trajectory shape only
   exists when the horizon exceeds the minimum. Pass `target_horizon_days` to buy that slack.
4. **Decision point — a horizon below $N_{\min}$ raises.** Do not "solve" it by widening
   the participation cap without re-deciding the cap on its own merits: raising the cap to
   fit a deadline converts a scheduling constraint into an impact cost silently.

## 3. Allocate the trajectory

Weights: `EQUAL_DAILY` is uniform; `FRONT_LOADED` decays at `profile_decay` per session;
`BACK_LOADED` grows at the same rate. Back-loaded weights are anchored so the largest is
1.0 — computing $e^{+0.3i}$ directly overflows a float past roughly 236 sessions.

Allocation is **water-filling**: find the scale $\lambda$ such that
$q_d = \min(\text{Cap},\, \lambda w_d)$ sums to $Q$. Sessions that reach the cap are frozen
and the remainder is re-shared among the rest in proportion to their weights.

> **Why not clip-and-refill?** Scaling weights to $Q$, clipping anything above the cap, then
> pushing the excess onto the first sessions with headroom does not preserve the trajectory.
> On a back-loaded 1,950,000-share order at a 100,000-share cap it produced
> `[100k × 12, 66_249.61, 83_750.39, 100k × 6]` — a schedule that rises, dips, then rises
> again. Water-filling returns a monotonically non-decreasing schedule for the same inputs.

Reporting slices to two decimals loses a fraction of a share per session, and that residue
is reconciled by **largest-remainder apportionment**, not by handing the whole residual to
one session. On a capacity-saturated schedule there may be nowhere legal to put it: 57
sessions each allocated 99.982536 shares under a 100-share cap lose 0.14 shares to flooring
while every session has only 0.02 shares of headroom. Each session therefore floors to a
whole quantum and the shortfall is handed out one quantum at a time to the sessions with the
largest discarded fractions, skipping any already at the cap. Ties resolve towards the end
of a back-loaded horizon and the start of a front-loaded one, so the reported trajectory does
not reverse at the quantum level. A schedule that still fails to balance raises rather than
being emitted.

Because slices are reported on a 0.01-share grid, the effective per-session ceiling is the
cap rounded **down** onto that grid; the minimum horizon is derived from the same figure so
every quantum has somewhere legal to go.

## 4. Cost the schedule

**Temporary impact, per session** (ATHL 2005): $K_d = \eta\,\sigma\,(q_d/V)^{\beta}$, with
$\beta = 0.6$. Reported per session in bps of that session's notional, and aggregated
quantity-weighted into bps of the parent notional:

$$\text{Temp}_{\text{bps}} = \frac{10^4}{Q}\sum_d q_d\,\eta\,\sigma\,(q_d/V)^{\beta}$$

Because $\beta < 1$ the cost is **convex in the rate**, so a uniform schedule has the lowest
temporary cost of the three profiles at a given horizon, and lengthening the horizon lowers
it further.

**Permanent impact** (ATHL 2005 + AC 2000 Eq. 8): the full price move is
$I = \gamma\,\sigma\,(Q/V)(\Theta/V)^{1/4}$ and a completed programme bears half of it.

> **Decision point — do not expect permanent impact to fall when you stretch the horizon.**
> Under linear permanent impact it is a function of total size only. If a longer horizon
> appears to reduce it, the model is mis-specified, not the trade.

## 5. Quantify the overnight risk

AC (2000) Eq. (5) with $\tau = 1$ session:

$$\text{Risk}_{1\sigma} = \sigma_{\text{daily}} \times P \times \sqrt{\textstyle\sum_d x_d^2}$$

where $x_d$ is the inventory carried out of session $d$. Reported in currency and in bps of
the parent notional so it sits in the same units as the impact estimate.

> **Decision point — this is a one-standard-deviation dispersion.** Roughly one horizon in
> three lands outside it and the tails are fatter than Gaussian. Do not present it as a
> maximum, and do not compare it against a 99% VaR limit without rescaling.

## 6. Choose the horizon

Run the engine across candidate horizons and read the two curves. Impact falls and risk
rises; there is no horizon that minimises both.

| Horizon | Temp impact | Perm impact | Overnight risk |
|---|---|---|---|
| $N_{\min}$ | highest | unchanged | lowest |
| $2N_{\min}$ | lower | unchanged | higher |
| $5N_{\min}$ | lower still | unchanged | highest |

Record which horizon was chosen and why alongside the order. When the tradeoff needs to be
resolved by an objective rather than by judgement, use the AC efficient frontier in
`implementation-shortfall-minimization`.

## 7. Operate the schedule

1. Map session indices onto the venue's calendar, skipping holidays and half-days. Half-days
   carry a fraction of normal volume, so the cap for those sessions must be scaled down.
2. Hand each session's target to an intraday algo (`execution-algo-twap-vwap-slicing`,
   `participation-of-volume-pov-execution`). This engine sets the day's budget; it does not
   place orders.
3. **Re-plan, do not accumulate.** After each session, re-run with the actual remaining
   quantity and refreshed ADV and volatility. Rolling an unfilled remainder forward without
   re-planning silently raises later-session participation above the cap.
4. Re-check feasibility before every event that compresses the horizon: earnings, index
   reviews, expiries, and any halt.
