# Workflows for Credit Card Signal Construction

1. **Panel Ingestion & Scaling**:
   - Ingest quarterly panel spend $S_{panel}$.
   - Compute Implied Revenue: $R_{implied} = S_{panel} \times \gamma_{scaling}$.
2. **Growth Metrics**:
   - $\text{YoY Growth} = \frac{R_{implied, t} - R_{implied, t-4}}{R_{implied, t-4}}$.
3. **Surprise Threshold Evaluation**:
   - $\text{Surprise Pct} = \frac{R_{implied} - R_{consensus}}{R_{consensus}} \times 100\%$.
4. **Signal Output**:
   - If $\text{Surprise Pct} \ge +2.5\% \implies$ `BEAT_BUY`.
   - If $\text{Surprise Pct} \le -2.5\% \implies$ `MISS_SELL`.
   - Else $\implies$ `NEUTRAL`.