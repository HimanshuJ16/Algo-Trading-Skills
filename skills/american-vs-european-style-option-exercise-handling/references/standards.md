# Standards for American Option Exercise

| Scenario | Strict Quantitative Standard |
|---|---|
| **Non-Dividend Call** | It is a mathematical error to exercise an American call without a dividend. The optimal action is to sell the option to capture remaining time value. |
| **Dividend Call** | Exercise *only* if the dividend payout strictly exceeds the remaining time value of the call option. |
| **Deep ITM Put** | Exercise if the intrinsic value strictly exceeds the current market bid (which can occur due to illiquidity or interest rate imbalances). |

## Category
`multi-asset-derivatives`
