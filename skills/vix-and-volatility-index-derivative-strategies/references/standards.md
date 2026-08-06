# Institutional VIX & Volatility Derivative Standards

## 1. VIX Term Structure State Classification Standard
| Term Structure State | Condition Formula | Market Environment | Primary Strategy Response |
| :--- | :--- | :--- | :--- |
| **CONTANGO** | $\frac{F_2 - F_1}{F_1} \ge +2.0\%$ | Calm Market / Equity Rally | **Short F1 Futures / Roll Yield Harvest** |
| **BACKWARDATION** | $\frac{F_2 - F_1}{F_1} \le -2.0\%$ | Market Sell-Off / Volatility Spike | **Long VIX OTM Call Spreads / Tail Protection** |
| **FLAT** | $-2.0\% < \frac{F_2 - F_1}{F_1} < +2.0\%$ | Transition Period | **Neutral / Cash** |

---

## 2. Quantitative Roll Yield & Decay Equations

### A. Front-Month Annualized Roll Yield:
$$\text{Roll Yield \%} = \left( \frac{F_1 - S_{\text{VIX}}}{S_{\text{VIX}}} \right) \times \left( \frac{365}{D_{\text{expiry}}} \right) \times 100$$

Where $S_{\text{VIX}}$ is Spot VIX, $F_1$ is Front-Month VIX Futures price, and $D_{\text{expiry}}$ is days to expiration.

### B. Daily Dollar Roll Decay ($D_{\text{usd}}$):
$$D_{\text{usd}} = \left( \frac{F_1 - S_{\text{VIX}}}{D_{\text{expiry}}} \right) \times N_{\text{contracts}} \times 1,000$$

Where $\$1,000$ is the Cboe VIX Futures Contract Multiplier per index point.

---

## 3. VIX Call Spread Hedging Payoff Standard
For a 1x1 Vertical VIX Call Spread with lower strike $K_1$ and upper strike $K_2$ ($K_2 > K_1$) priced off forward futures price $F_1$:

$$\text{Max Profit per Contract} = (K_2 - K_1) \times \$1,000$$

$$\text{Max Loss per Contract} = \text{Net Debit Paid} \times \$1,000$$

$$\text{Breakeven VIX at Expiration} = K_1 + \text{Net Debit Paid}$$