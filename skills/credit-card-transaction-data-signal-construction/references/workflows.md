# Workflows for Credit Card Signal Construction

1. **Panel Ingestion & Scaling**:
   - Ingest quarterly panel spend $S_{panel}$ (as-delivered, point-in-time snapshot).
   - Compute Implied Revenue: $R_{implied} = S_{panel} \times \gamma_{scaling}$.
   - Decision point: if the panel's coverage universe changed since $\gamma$ was
     calibrated (vendor onboarding/churn of card issuers), recalibrate against
     the last two reported 10-Q revenues before generating signals.

2. **Growth Metrics** (seasonality-aligned quarters, $t$ vs $t-4$):
   - $\text{YoY Growth} = \frac{R_{implied, t} - R_{implied, t-4}}{R_{implied, t-4}}$.
   - Alignment is enforced: `YYYY-Qn` labels that are not exactly four
     quarters apart raise `ValueError`; other label schemes only warn, so
     verify fiscal alignment (including 53-week restatements) upstream.
   - Decompose via `decompose_growth`: $g_{ticket}$ from average ticket
     (spend / transaction count) and $g_{volume}$ from transaction count,
     linked by $(1 + g_{spend}) = (1 + g_{ticket})(1 + g_{volume})$.
   - Decision point: $g_{volume}$ collapsing with stable $g_{ticket}$ → suspect
     panel composition shift; treat the growth signal as unreliable until
     $\gamma$ is recalibrated.

3. **Surprise Threshold Evaluation**:
   - $\text{Surprise Pct} = \frac{R_{implied} - R_{consensus}}{R_{consensus}} \times 100\%$
     with point-in-time consensus (restated estimates leak information).
   - Threshold is a default, not a constant of nature: calibrate $\pm 2.5\%$
     against the panel's historical absolute prediction error before trusting
     directional signals.

4. **Signal Output** (boundaries inclusive, evaluated on the unrounded
   surprise; the reported value is rounded to 2 dp):
   - If $\text{Surprise Pct} \ge +2.5\% \implies$ `BEAT_BUY`.
   - If $\text{Surprise Pct} \le -2.5\% \implies$ `MISS_SELL`.
   - Else $\implies$ `NEUTRAL`.
   - `confidence_score` is a heuristic rank, not a probability: use it to
     sort signals for review, never to size positions.
