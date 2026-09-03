# Variance Swap & Volatility Derivative Standards

All formulas below are stated in **volatility points squared**: a 20% annualized
volatility is $K_{\text{vol}} = 20.0$ and $K_{\text{var}} = 400.0$. Where a formula is
naturally in decimal variance, the $\times 10{,}000$ conversion is shown explicitly.

Primary source throughout: Demeterfi, Derman, Kamal & Zou, *More Than You Ever Wanted
To Know About Volatility Swaps*, Goldman Sachs Quantitative Strategies Research Notes,
March 1999 (**DDKZ**), <https://emanuelderman.com/wp-content/uploads/1999/02/gs-volatility_swaps.pdf>.
Equation and table numbers refer to that paper.

---

## 1. Payoff and notional conversion

Variance swap payoff at maturity $T$ (DDKZ page 3):

$$\text{Payoff}_{\text{var}} = N_{\text{var}} \times \left(\sigma^2_{\text{realized}} - K_{\text{var}}\right)$$

A volatility swap pays $N_{\text{vega}}(\sigma_R - K_{\text{vol}})$ (DDKZ Equation 1),
where $N_{\text{vega}}$ is quoted in dollars per volatility point. DDKZ Equation 43
linearizes the variance payoff about $K_{\text{vol}}$:

$$\sigma_R - K_{\text{vol}} \approx \frac{1}{2K_{\text{vol}}}\left(\sigma_R^2 - K_{\text{vol}}^2\right)
\quad\Longrightarrow\quad
N_{\text{var}} = \frac{N_{\text{vega}}}{2 K_{\text{vol}}}$$

At $K_{\text{vol}} = 20$ the two notionals differ by a factor of 40. The
approximation is exact in value and in first derivative at $\sigma_R = K_{\text{vol}}$
and degrades quadratically away from it — you cannot fit a line everywhere with a
parabola (DDKZ Figure 11).

---

## 2. Fair variance strike by static log-contract replication

### 2.1 Continuum form

$$K_{\text{var}} = \frac{2}{T}e^{rT}\left[\int_0^{S^*}\frac{P(K)}{K^2}dK + \int_{S^*}^{\infty}\frac{C(K)}{K^2}dK\right] + \frac{2}{T}\left[rT - \left(\frac{S_0e^{rT}}{S^*} - 1\right) - \ln\frac{S^*}{S_0}\right]$$

This is **DDKZ Equation 27**. $S^*$ is the reference level splitting the put wing from
the call wing; DDKZ replicate the log payoff as a forward struck at $S^*$ plus calls
above and puts below, each weighted by the inverse square of its strike (DDKZ
Figure 4).

**The bracketed term is not decoration.** Writing $x = F/S^*$ with $F = S_0e^{rT}$, it
reduces to $\frac{2}{T}\left[-(x-1) + \ln x\right]$, which vanishes **only** when
$S^* = F$. Expanding $\ln x$ about $x = 1$ gives $-(x-1)^2/2$, so

$$\frac{2}{T}\left[rT - \left(\frac{F}{S^*} - 1\right) - \ln\frac{S^*}{S_0}\right] \approx -\frac{1}{T}\left(\frac{F}{S^*} - 1\right)^2$$

which is exactly the correction term in the Cboe index formula of §2.3.

### 2.2 Discrete form used by the engine

$$K_{\text{var}} \approx \left\{\frac{2}{T}e^{rT}\sum_{i}\frac{\Delta K_i}{K_i^2}Q(K_i) + \frac{2}{T}\left[rT - \left(\frac{F}{S^*} - 1\right) - \ln\frac{S^*}{S_0}\right]\right\} \times 10{,}000$$

with $S^* = K_0$, the largest **available** strike at or below $F$, and:

| Quantity | Rule |
|---|---|
| $Q(K_i)$, $K_i < K_0$ | OTM put price |
| $Q(K_i)$, $K_i > K_0$ | OTM call price |
| $Q(K_0)$ | average of the $K_0$ put and the $K_0$ call |
| $\Delta K_i$, interior | $(K_{i+1} - K_{i-1})/2$ |
| $\Delta K_i$, first/last | distance to the single adjacent strike |

$\Delta K_i$ must be built over the **deduplicated, OTM-selected** strike grid. A raw
two-sided chain lists each strike twice; computing $\Delta K$ over it halves every
interior spacing.

### 2.3 Cboe index variant (cross-check, not a substitute)

Cboe's published index mathematics uses the second-order form of the same correction:

$$\sigma^2 = \frac{2}{T}\sum_i\frac{\Delta K_i}{K_i^2}e^{RT}Q(K_i) - \frac{1}{T}\left[\frac{F}{K_0} - 1\right]^2$$

with $K_0$ the "first strike equal to or otherwise immediately below $F$", $Q(K_0)$ the
average of the $K_0$ put and call, and the same $\Delta K_i$ edge rules. Source: *Cboe
Volatility Index Mathematics Methodology*, formula (1) and the accompanying variable
table, <https://cdn.cboe.com/api/global/us_indices/governance/Cboe_Volatility_Index_Mathematics_Methodology.pdf>.
Cboe also derives $F$ from put–call parity at the strike of minimum $|C - P|$ rather
than from $S_0e^{rT}$; the engine here takes $S_0$ and $r$ directly.

### 2.4 Replication error from a truncated strike range

Truncation is a **one-way downward bias** and it scales with maturity. DDKZ Table 4,
flat 25% implied volatility, $r = 5\%$, strikes one point apart:

| Expiration | Strikes 50%–200% of spot | Strikes 75%–125% of spot |
|---|---|---|
| Three-month | $(25.0)^2$ | $(24.9)^2$ |
| One-year | $(25.0)^2$ | $(23.0)^2$ |

Beyond the strike range the replicating portfolio's vega and gamma fall to zero and it
stops accruing variance; over a longer horizon the underlying is more likely to get
there. There is no correction for this inside the formula — the fix is more strikes.

### 2.5 Jump risk

Replication is exact only for a continuous path. For a single downward jump of size
$J$ (a 10% gap down is $J = 0.1$), the P&L of a short variance swap hedged with the
replication strategy is $\frac{2}{T}\left[-J - \ln(1-J)\right] - \frac{J^2}{T}$ (DDKZ
Equations 39–40), whose leading term is **cubic**:

$$\text{P\&L} \approx \frac{2}{3T}J^3$$

The sign flips with the direction of the jump (DDKZ Equations 41–42). DDKZ Table 5
quantifies it: a 10% downward jump is worth 7.2 variance points on a one-year swap and
28.8 on a three-month swap, per $1 of variance notional.

---

## 3. Realized variance

Given prices $S_0, S_1, \dots, S_N$ observed on the schedule named in the
confirmation:

$$r_i = \ln\frac{S_i}{S_{i-1}}, \qquad \sigma^2_{\text{realized}} = \left(\frac{A}{N}\sum_{i=1}^{N}r_i^2\right)\times 10{,}000$$

DDKZ (page 2) require the contract to specify three things, all of which change the
number:

1. **Source and observation frequency** — e.g. official daily closing prices of the
   index.
2. **Annualization factor $A$** — DDKZ's own example uses 260 business days; 252 is the
   common US convention. Match the term sheet.
3. **Mean treatment** — the sample mean is **not** subtracted. DDKZ: the zero-mean
   method "is theoretically preferable, because it corresponds most closely to the
   contract that can be replicated by options portfolios."

$N$ is the *expected* observation count for the accrual period in a settlement
calculation, and disrupted days follow the confirmation's disruption provisions. The
engine divides by the count actually observed, which is the accrual-to-date figure,
not the settlement figure.

---

## 4. Seasoned mark-to-market

Variance is additive in time, so the accrued and forward legs blend linearly:

$$V_{\text{exp}} = \frac{t}{T}\sigma^2_{\text{realized, elapsed}} + \frac{T-t}{T}K_{\text{var, remaining}}$$

$$\text{MTM} = e^{-r(T-t)} \times N_{\text{var}} \times \left(V_{\text{exp}} - K_{\text{var, strike}}\right)$$

$K_{\text{var, remaining}}$ is replicated per §2 from an option strip expiring at the
swap's maturity, priced with the **valuation-date** spot and rate. Using the inception
spot moves the forward and therefore the $K_0$ boundary. Because a swap struck at fair
value has zero value at inception, this mark is also the unrealized P&L.

---

## 5. Volatility swap strike and the convexity correction

There is no static replication of a volatility swap; variance is the primary
underlyer and volatility is a nonlinear (square-root) claim on it (DDKZ, *From
Variance to Volatility Contracts*).

The naive estimate $K_{\text{vol}} = \sqrt{K_{\text{var}}}$ (DDKZ Equation 44) is
**not** arbitrage-free: with that strike the variance swap payoff exceeds the
volatility swap payoff at every realized volatility, by

$$\text{convexity bias} = \frac{1}{2K_{\text{vol}}}\left(\sigma_R - K_{\text{vol}}\right)^2 \ge 0$$

so the fair volatility strike must satisfy $K_{\text{vol}} < \sqrt{K_{\text{var}}}$.
Sizing the gap requires a view on the level *and* the volatility of future realized
volatility.

DDKZ Appendix D Equation D4 takes realized volatility as normally distributed,
$\Sigma_T \sim N(\Sigma, \sigma_\Sigma)$. Then
$K_{\text{var}} = \mathbb{E}[\Sigma_T^2] = \Sigma^2 + \sigma_\Sigma^2$ exactly, giving
the closed form the engine implements:

$$K_{\text{vol}} = \sqrt{K_{\text{var}} - \sigma_\Sigma^2}, \qquad \text{convexity adjustment} = K_{\text{var}} - K_{\text{vol}}^2 = \sigma_\Sigma^2$$

with $\sigma_\Sigma$ the standard deviation of realized volatility in volatility
points. For small $\sigma_\Sigma$ this agrees with the second-order Taylor expansion
of $\mathbb{E}[\sqrt{V}]$ about $\mathbb{E}[V]$,

$$K_{\text{vol}} \approx \sqrt{K_{\text{var}}} - \frac{\operatorname{Var}(V)}{8\,K_{\text{var}}^{3/2}}$$

— note the numerator is the variance of realized **variance** $V$, not of volatility.
The normal-volatility model is an assumption, not a market fact; it only makes sense
while the probability of negative volatility is negligible (DDKZ Appendix D).
