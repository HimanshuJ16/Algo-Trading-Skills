# Risk-Control Latency Standards

- Use synchronized, documented clock domains. Reject non-monotonic, NaN, infinite, or cross-domain timestamp sequences; never clamp them to zero.
- Define event, decision, dispatch, acknowledgement, cancellation, and effective-containment semantics separately. Local dispatch is not proof of broker/exchange action.
- Set budgets per control, scope, session, and failure action from measured capacity evidence; illustrative values are not production policy.
- Alert on breach, uncertain measurements, queue age, missing acknowledgements, clock skew, retries, stale control configuration, and actuator failure.
- Keep critical-path measurement bounded and non-blocking. Export asynchronously and expose retention drops/overwrites.
- Segment percentile windows by control and scope, with an explicit sample count. Preserve raw breach evidence for investigation.
- On a safety-critical breach, invoke and verify the approved fail-safe action; an alert alone is insufficient.
