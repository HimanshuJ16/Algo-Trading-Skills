# Pre-Flight Checklist

- [ ] Are RMSE tracking error and mean prediction bias calculated on trade history?
- [ ] Are recalibration thresholds ($\text{RMSE} > 3.5\text{ bps}$, $|\bar{\epsilon}| > 1.5\text{ bps}$) configured?
- [ ] Is least-squares parameter refitting executed on recent trade sample data?
- [ ] Are updated impact parameters ($\eta^*, \gamma^*$) validated before updating production models?
