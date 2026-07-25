# Pre-Flight Checklist

- [ ] Is the dataset strictly sorted by time before applying the point-in-time target encoder?
- [ ] Have you verified that no future target data is leaking into the encoding (e.g., checking if the first row's encoding is exactly the global prior)?
- [ ] Is the smoothing weight parameter tuned appropriately for the frequency of your data (e.g., higher for minute-bar data, lower for daily)?
