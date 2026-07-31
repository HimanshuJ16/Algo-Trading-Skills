# Pre-Flight Checklist

- [ ] Are subnets categorized into Zero-Trust security zone tiers?
- [ ] Is direct traffic from `PUBLIC_DMZ` to `TRADING_EXECUTION` or `KEY_CUSTODY` blocked?
- [ ] Are administrative ports (22, 3389) protected from public internet exposure?
- [ ] Is `KEY_CUSTODY` subnet ingress whitelisted to authorized MPC/HSM signers?