# Standards for Smart Contract Approval Scope Minimization

| Pattern | Standard Policy |
|---|---|
| Unlimited Allowance | `uint256.max` approvals MUST be hard-blocked. |
| Allowance Sizing | Approvals MUST be sized to exact transaction notional. |
| EIP-2612 Permit | Off-chain signed permits MUST use deadline $\le 600$ seconds. |