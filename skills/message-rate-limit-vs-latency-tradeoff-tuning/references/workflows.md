# Workflows for Message Rate Limit Tuning

All rates are messages per second (MPS); all delays are milliseconds.

## Model and its assumptions

The engine models a quoting session in **steady state**:

- Each reprice cycle emits **one outbound message per quoting side**, i.e. a
  cancel/replace amendment. A strategy that sends a separate cancel plus a separate new
  order emits two per side and must double `active_quoting_pairs`.
- With a reprice delay $\Delta t$ (ms), sustained quote traffic is capped at
  $1{,}000 \times \text{pairs} / \Delta t$ MPS.
- The throttle cannot manufacture ticks. Realised quote traffic is
  $\min(R_{\text{unthrottled}},\ \text{throttle cap})$, so a quiet symbol under a
  1 ms floor projects its actual arrival rate, not the 2,000 MPS the floor would permit.
- Rate is an **average**. Venues measure over a rolling window (CME evaluates
  administrative thresholds over three seconds), so this model says nothing about
  sub-window bursts.

## 1. Unthrottled Rate & Target Limit Audit

1. Validate the budget before arithmetic. `exchange_max_mps`, `target_safety_buffer_pct`,
   `min_reprice_delay_ms`, and `max_reprice_delay_ms` must be finite and strictly
   positive; the buffer must be $\le 100\%$; `max_reprice_delay_ms` must be $\ge$
   `min_reprice_delay_ms`. `ticks_per_sec`, `price_volatility_bps`, and
   `baseline_session_mps` must be finite and non-negative. A NaN tick rate silently
   compares `False` against every threshold, so an unvalidated NaN reads as "no tuning
   required" — reject it at construction instead.
2. $R_{\text{target}} = R_{\text{max}} \times \text{safety\_pct} / 100$.
3. $R_{\text{unthrottled}} = \text{TicksPerSec} \times \text{ActiveQuotingPairs}$.
4. $R_{\text{available}} = R_{\text{target}} - R_{\text{baseline}}$ — the budget left after
   co-resident flow on the same session (RTS 6 Art. 15(2) counts all orders sent to the
   venue, not just this strategy's).
5. Tuning is required when $R_{\text{unthrottled}} + R_{\text{baseline}} > R_{\text{target}}$.

## 2. Optimal Quote Reprice Delay Calculation

Solve the cap for the delay that lands exactly on the available budget:

$$\Delta t_{\text{required}} = \frac{1{,}000 \times \text{ActiveQuotingPairs}}{R_{\text{available}}}$$

then **round up** to the reported precision (0.01 ms) and clamp into
$[\Delta t_{\text{min}}, \Delta t_{\text{max}}]$.

The rounding direction is load-bearing. With a 300 MPS budget and one quoting pair the
required delay is $3.\overline{3}$ ms. Rounding to nearest gives 3.33 ms, which sustains
$1000/3.33 = 300.3$ MPS — over budget on every cycle, forever. Rounding up gives 3.34 ms
and 299.4 MPS. Always round a throttle interval away from the limit.

If $R_{\text{available}} \le 0$, co-resident flow alone has consumed the budget; skip the
division entirely and go to the feasibility gate with $\Delta t_{\text{max}}$.

## 3. Feasibility Gate

$$R_{\text{projected}} = \min\left(R_{\text{unthrottled}},\ \frac{1{,}000 \times \text{ActiveQuotingPairs}}{\Delta t_{\text{optimal}}}\right) + R_{\text{baseline}}$$

If $R_{\text{projected}} > R_{\text{target}}$, the clamp bound rather than the budget
determined the delay. Emit `RATE_LIMIT_TARGET_UNREACHABLE` with
`is_target_achievable = False`.

This branch is not cosmetic. With 1,000 quoting pairs against a 400 MPS target the
required delay is 2,500 ms; clamped to a 500 ms staleness ceiling the strategy sustains
2,000 MPS — four times the 500 MPS session ceiling. Reporting that as
`RATE_LIMIT_TUNING_APPLIED` hands the caller a parameter set that will be rejected and
then disconnected. Remediation is upstream of this module: fewer quoting pairs, a wider
`price_threshold_bps`, or the flow split across additional sessions.

Consumers must branch on `is_target_achievable`. Treating "not a direct pass" as "tuned
successfully" reintroduces exactly this failure.

## 4. Adverse Selection Exposure Audit

$$\text{Score} = \Delta t_{\text{optimal}} \times \sigma_{\text{bps}}$$

An ordinal comparator in bps-milliseconds — see the quantitative note in
`standards.md` for why it is not a cost and why true pick-off exposure scales with
$\sqrt{\Delta t}$. Use it to compare candidate delays for one symbol under one volatility
regime; re-run the tuner when volatility moves rather than carrying a calm-market delay
into a spike.

## 5. Audit Report Generation

Output a `TuningReport`. Statuses:

| Status | Meaning | Deployable |
|---|---|---|
| `DIRECT_PASS_NO_TUNING_REQUIRED` | Session traffic is already inside the target. Minimum delay retained. | Yes |
| `RATE_LIMIT_TUNING_APPLIED` | Delay raised; projected rate is inside the target. | Yes |
| `RATE_LIMIT_TARGET_UNREACHABLE` | No permitted delay brings the session inside the target. Logged at `ERROR`. | **No** |

Invariant to assert in any integration test: whenever `is_target_achievable` is `True`,
`projected_message_rate_mps <= target_safety_limit_mps`.
