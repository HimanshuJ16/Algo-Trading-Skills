# Institutional Variance Swap & Volatility Derivative Standards

## 1. Demeterfi, Derman, Kamal, & Zou (1999) Static Log-Contract Replication
A Variance Swap payoff at maturity $T$ is given by:
$$\text{Payoff}_{\text{var}} = N_{\text{var}} \times (\sigma^2_{\text{realized}} - K_{\text{var}})$$

Where $N_{\text{var}}$ (Variance Notional) is derived from $N_{\text{vega}}$ (Vega Notional):
$$N_{\text{var}} = \frac{N_{\text{vega}}}{2 K_{\text{vol}}}$$

### Continuous Log-Contract Formula:
$$K_{\text{var}} = \frac{2}{T} e^{r T} \left[ \int_0^{F_0} \frac{1}{K^2} P(K) dK + \int_{F_0}^\infty \frac{1}{K^2} C(K) dK \right] - \frac{1}{T} \left[ \frac{F_0}{S_0} - 1 - \ln \left( \frac{F_0}{S_0} \right) \right]$$

### Discretized Numerical Integration Across Strike Grid $\Delta K_i$:
$$K_{\text{var}} \approx \frac{2}{T} e^{r T} \sum_{i=1}^{M} \frac{\Delta K_i}{K_i^2} Q(K_i) - \frac{1}{T} \left[ \frac{F_0}{S_0} - 1 - \ln \left( \frac{F_0}{S_0} \right) \right] \times 10,000$$

Where $Q(K_i)$ is the OTM Put price $P(K_i)$ for $K_i < F_0$ and OTM Call price $C(K_i)$ for $K_i \ge F_0$.

---

## 2. Realized Log-Return Variance Standard
Given daily closing prices $S_0, S_1, \dots, S_N$:
$$r_i = \ln \left( \frac{S_i}{S_{i-1}} \right)$$

$$\sigma^2_{\text{realized}} = \left( \frac{252}{N} \sum_{i=1}^N r_i^2 \right) \times 10,000$$

---

## 3. Seasoned Contract Mark-to-Market (MTM) Valuation Formula
For an active contract with total maturity $T$, elapsed time $t$, and remaining time $T - t$:
$$\text{Expected Total Variance } V_{\text{exp}} = \left( \frac{t}{T} \right) \sigma^2_{\text{realized, elapsed}} + \left( \frac{T - t}{T} \right) K_{\text{var, remaining}}$$

$$\text{MTM}_{\text{present\_value}} = e^{-r(T-t)} \times N_{\text{var}} \times (V_{\text{exp}} - K_{\text{var, initial}})$$

---

## 4. Volatility Swap vs Variance Swap Convexity Adjustment
Because $\mathbb{E}[\sqrt{V}] \le \sqrt{\mathbb{E}[V]}$ by Jensen's Inequality:
$$K_{\text{vol}} \approx \sqrt{K_{\text{var}}} - \frac{\text{Var}(\sigma_{\text{realized}})}{8 K_{\text{var}}^{3/2}}$$
Convexity Adjustment $= K_{\text{var}} - K_{\text{vol}}^2 > 0$.