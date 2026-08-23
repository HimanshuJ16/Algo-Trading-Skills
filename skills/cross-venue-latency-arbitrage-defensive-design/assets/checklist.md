# Pre-Flight Checklist

- [ ] Is the imbalance-weighted mid recomputed on every L1 book update, with no rounding applied before the toxicity difference is taken?
- [ ] Is the toxicity threshold calibrated from the firm's own realized adverse-selection data rather than copied from a document?
- [ ] Does the tick-denominated threshold respect the bound $\tau_{\text{ticks}} \le$ half-spread in ticks — i.e. can it actually fire in the spread regime you quote in?
- [ ] Is a scale-free normalized score used wherever thresholds must transfer across instruments or tick regimes?
- [ ] Is the exposed side identified, and is widening applied asymmetrically to that side only?
- [ ] Is spread widening bounded (scaled on the normalized score), so a wide book cannot produce an absurd quoted spread?
- [ ] Are cross-venue latency margins measured with one-way delivery times, from one synchronized clock domain, and audited against measured percentiles rather than means?
- [ ] Is a dead heat ($\Delta t_{\text{margin}} = 0$) treated as a loss, with a jitter buffer on top?
- [ ] When the race is lost, does the recommended quote size go to 0 rather than merely shrinking?
- [ ] Are locked and crossed books ($P_{\text{bid}} \ge P_{\text{ask}}$) detected explicitly instead of scoring as zero toxicity?
- [ ] Are non-positive tick sizes, negative depth, and non-finite prices or latencies rejected at the boundary?
- [ ] Do downstream consumers branch on `trigger_reasons` rather than parsing recommendation text?
- [ ] Has the defensive pull policy been reconciled with the market-making agreement's presence obligation (RTS 8) and the venue's order-to-trade-ratio limits (RTS 9)?
