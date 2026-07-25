# Pre-Flight Checklist

- [ ] Is the trading bot using AppRole (or equivalent service identity) rather than a human's personal Vault token?
- [ ] Is the Vault client enforcing environment isolation (e.g., Dev bots cannot read Prod secrets)?
- [ ] Are secrets cached in memory to avoid excessive API calls to Vault?
- [ ] Have you verified that printing the loaded configuration object does not leak the raw secrets?
