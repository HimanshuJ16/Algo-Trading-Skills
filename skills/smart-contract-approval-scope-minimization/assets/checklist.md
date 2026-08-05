# Pre-Flight Checklist

- [ ] Are unlimited `uint256.max` approvals strictly blocked?
- [ ] Are approval amounts sized to exact required transaction notionals?
- [ ] Is EIP-2612 off-chain permit utilized when supported by the token?
- [ ] Are active wallet allowances audited and reset to zero when stale?