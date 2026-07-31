# Pre-Flight Checklist

- [ ] Is underlying spot price shift evaluated against the recalculation threshold?
- [ ] Is Taylor expansion ($\Delta + \Gamma \cdot \Delta S$) applied for micro-ticks?
- [ ] Is full Black-Scholes revaluation triggered for price shifts exceeding $0.5\%$?
- [ ] Are net portfolio Delta, Gamma, and Vega aggregated in real time?