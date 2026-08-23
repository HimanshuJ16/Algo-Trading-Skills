# Workflows for Cross-Venue Latency Arbitrage Defensive Design

1. **Lead Venue Telemetry Parsing**:
   - Validate the book first: `tick_size > 0`, finite prices, non-negative depth.
     Negative depth can drive the weighted mid outside $[P_{\text{bid}}, P_{\text{ask}}]$
     and invert the defensive signal.
   - Weighted Mid: $P_w = \frac{V_{\text{ask}} P_{\text{bid}} + V_{\text{bid}} P_{\text{ask}}}{V_{\text{bid}} + V_{\text{ask}}}$
     (the *weighted mid*, not Stoikov's micro-price - see `references/standards.md`).
   - No displayed depth on either side $\implies$ no imbalance information $\implies$ return the mid.
   - Do not round: rounding to 4 decimals zeroes the signal on 5- and 8-decimal instruments.
2. **Toxicity & Latency Margin Evaluation**:
   - Normalized (primary, scale-free):
     $\tau_{\text{norm}} = \left|\frac{V_{\text{bid}} - V_{\text{ask}}}{V_{\text{bid}} + V_{\text{ask}}}\right| \in [0,1]$,
     which equals $|P_w - P_{\text{mid}}| / (\text{spread}/2)$ but stays exactly zero on a
     balanced book instead of carrying float residue.
   - Legacy tick score: $\tau_{\text{ticks}} = \tau_{\text{norm}} \cdot \text{half-spread in ticks}
     = |P_w - P_{\text{mid}}| / \text{TickSize}$, bounded above by the half-spread in ticks.
   - Exposed side: $P_w > P_{\text{mid}}$ (buying pressure) $\implies$ the resting **ASK** is
     lifted; $P_w < P_{\text{mid}}$ $\implies$ the resting **BID** is hit.
   - $\Delta t_{\text{margin}} = (t_{\text{lead}} + t_{\text{sweep}}) - (t_{\text{cancel\_sent}} + t_{\text{cancel\_delivery}})$,
     all one-way, all from one synchronized clock domain. A cancel timestamped before
     the lead event raises rather than inflating the margin.
3. **Defensive Action Trigger** - fire when any of:
   - $P_{\text{bid}} \ge P_{\text{ask}}$ (locked/crossed) $\implies$ maximum defense, size 0;
   - $\Delta t_{\text{margin}} \le$ `latency_safety_margin_us` (a dead heat is a loss)
     $\implies$ `LATENCY_RACE_LOST`, size 0 - widening cannot protect a quote that cannot
     be pulled;
   - $\tau_{\text{ticks}} \ge$ the tick threshold, or $\tau_{\text{norm}} \ge$ the calibrated
     normalized threshold when one is configured.
4. **Quote Geometry**:
   - Widen only the exposed side: $S_{\text{exposed}} = S_{\text{base}}(1 + k\,\tau_{\text{norm}})$,
     bounded by $S_{\text{base}}(1+k)$; the protected side stays at $S_{\text{base}}$.
   - Taper size: $Q = \max\left(0, \lfloor Q_{\text{base}}(1 - \tau_{\text{norm}}) \rfloor\right)$,
     reaching 0 as the weighted mid pins against the touch.
5. **Audit Reporting**:
   - Emit `LatencyArbitrageDefenseReport` with both toxicity measures, `half_spread_ticks`,
     `toxic_side`, and machine-readable `trigger_reasons`; consumers branch on the reason
     codes, since a toxicity trigger (skew quotes) and a lost race (stop quoting) demand
     different responses.
6. **Operational Governance** (outside this module):
   - Reconcile defensive pulls against the market-making agreement's presence obligation
     and against the venue's order-to-trade-ratio limits before deploying.
