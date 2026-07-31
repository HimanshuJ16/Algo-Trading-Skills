# Pre-Flight Checklist

- [ ] Are nanosecond timestamps delta-encoded ($\Delta t_i = t_i - t_{i-1}$)?
- [ ] Is Parquet Zstandard / Snappy columnar compression enabled?
- [ ] Is target compression ratio $\ge 5.0\times$ achieved?
- [ ] Are storage tiers (Hot, Warm, Cold) assigned based on data age?
