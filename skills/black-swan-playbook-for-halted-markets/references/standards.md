# Standards — black-swan-playbook-for-halted-markets

| Exchange Halt Event | Required Playbook Action | Target SLA |
|---|---|---|
| LULD Volatility Halt | Cancel open orders + Proxy ETF hedge | $\le 10$ ms |
| Market-Wide Circuit Breaker (L1/L2) | Pause strategy + Hedge open beta | Immediate |
| Re-Opening Auction | Place auction limit order | Before auction freeze |

## Category

`risk-management`
