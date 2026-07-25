# Broker Integration Standards — post-only-and-maker-taker-fee-optimization

| Protocol / Exchange | Post-Only Parameter | TIF Standard |
|---|---|---|
| Binance / Bybit | `postOnly: true` | `POC` (Post-or-Cancel) |
| Coinbase Advanced | `post_only: true` | `LIMIT_GTD` with post_only flag |
| FIX Protocol / IBKR | `execInst="6"` | `ParticipateDoNotInitiate` |

## Category

`broker-integration` — see top-level `mappings/` directory.
