# Pre-Flight Checklist

- [ ] Is CPU frequency governor configured to `"performance"`?
- [ ] Are dedicated trading CPU cores isolated via `isolcpus`?
- [ ] Are CPU C-states disabled (`max_cstate=0`)?
- [ ] Are socket buffers set to $128\text{ MB}$ (`rmem_max` / `wmem_max`)?
- [ ] Is PTP IEEE 1588 daemon (`ptp4l`) active?
