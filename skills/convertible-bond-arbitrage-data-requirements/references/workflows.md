# Workflows for Convertible Bond Arbitrage Data & Metrics

Notation: `S` = stock spot, `Cr` = conversion ratio (shares per bond), `P_clean` = CB
clean price in currency per bond, `AI` = accrued interest per bond, `P_full = P_clean +
AI`, `Δ` = per-share equity delta in `[0, 1]`, `CS` = issuer credit spread in bp,
`r_f` = risk-free rate, `r_repo` = CB financing rate, `r_borrow` = stock loan fee,
`q` = expected dividend yield. All rates annualized decimals; all amounts per bond.

## 1. Market data ingestion and completeness audit

Required per instrument, per evaluation:

| Input | Source | Failure mode if stale/absent |
|---|---|---|
| `S` | equity SIP / venue feed | parity and the whole hedge size are wrong |
| `P_clean`, `AI` | CB dealer runs / evaluated pricing | premium wrong; carry denominator wrong |
| `Cr`, coupon, frequency, maturity | terms sheet / reference data | every metric wrong; ratio nominal mismatch is silent |
| `r_borrow`, borrow availability | prime broker / stock loan desk | carry overstated; recall risk invisible |
| `q` | corporate action / dividend forecast feed | carry overstated on the short leg |
| `CS` (bp) | CDS or issuer spread curve | no bond floor; no busted-convert detection |
| `Δ`, IV | CB model or vendor analytics | hedge mis-sized; vol screen meaningless |
| `r_repo`, short-proceeds rate and haircut | financing desk / PB agreement | carry sign can flip |

Audit rule: distinguish **missing** (absent) from **invalid** (NaN, infinite, negative,
`Δ ∉ [0, 1]`). Never substitute a default for a missing credit spread — a zero spread
silently inflates the bond floor and, with it, apparent downside protection.

## 2. Parity and conversion premium

```
Parity   = Cr * S
Premium% = (P - Parity) / Parity * 100      P = P_clean (default) or P_full
```

Equivalent market form: market conversion price `= P / Cr`, market conversion premium
per share `= P / Cr - S`, premium ratio `= (P / Cr - S) / S`. Both forms give the same
number; the ratio form makes the "how far the stock must rally to break even on
conversion" reading explicit.

The `P_clean` versus `P_full` choice is a reporting convention, not a correctness
question — but it must be stated, since the two differ by up to a full coupon period of
accrual. This repo's implementation defaults to the clean basis and exposes
`premium_basis="full"` for desks that quote dirty.

## 3. Delta hedge sizing

```
Short shares = CB quantity * Cr * Δ          (Δ = per-share delta, [0, 1])
```

Two delta conventions circulate:

| Convention | Range | Correct use |
|---|---|---|
| Per-share delta (`Δ%`) | `[0, 1]` | multiply by `Cr` as above |
| Shares-per-bond delta | `[0, Cr]` | already in shares; do **not** multiply by `Cr` |

They are related by `Δ_per_share = Δ_shares_per_bond / Cr`. Confusing them over-hedges
by a factor of `Cr`. Validate the range at the boundary of your system.

Round the result to the venue lot size and record the rounding residual
(`CB quantity * Cr * Δ - shares shorted`) as known, deliberate open delta — it is small
per package but accumulates across a book.

Rebalance on delta drift. A drift band is a cost/risk trade-off between gamma slippage
and transaction plus borrow costs; it is calibrated per name and liquidity, not fixed by
any standard. Wider bands leave more directional risk; narrower bands pay away more of
the gamma P&L in spread and fees.

## 4. Bond floor (investment value) from the credit spread

```
y      = r_f + CS / 10_000
Floor  = Σ_k (coupon/f) / (1 + y/f)^(t_k * f)  +  Par / (1 + y/f)^(T * f)
```

with cash-flow times `t_k` laid out backwards from maturity `T` in `1/f` steps so a stub
first period is priced at its actual remaining time. The floor ignores the conversion
option by construction: it is what the CB is worth if the option expires worthless and
the issuer performs. The theoretical minimum value of the convertible is
`max(parity, floor)`.

Busted-convert screen: when `parity < ratio * floor` (ratio is a configured heuristic,
default 0.5), the instrument trades on credit, delta is small, and equity-vol screening
does not apply.

## 5. Carry audit of the hedged package

Per bond, annualized, in currency:

```
short_MV       = Δ * Parity
+ coupon       = coupon_rate * Par
+ short_rebate = r_proceeds * credit_ratio * short_MV
- financing    = r_repo * P_full
- borrow       = r_borrow * short_MV
- dividends    = q * short_MV
= net_carry ;  net_carry_rate = net_carry / P_full
```

Two things this decomposition gets right that a rate-on-bond-notional shortcut does not:

1. The hedge-leg costs scale with the short position, so they fall with delta. At
   `Δ = 0.60`, `Parity = 900`, `P_full = 1000`, the borrow base is 540, not 1,000.
2. Interest earned on short-sale proceeds is a real inflow. Modelled here as a gross
   rate less the separately charged loan fee, so the net rebate is
   `r_proceeds - r_borrow` — negative for hard-to-borrow names. Prime brokers also
   haircut the proceeds balance they pay on; that is the `credit_ratio` term.

## 6. Trade decision

A candidate passes when implied volatility is cheap to realized by a configured margin,
the conversion premium is inside the configured band, net carry clears the configured
floor, and the convert is not busted. Those thresholds are desk heuristics — calibrate
them. Screening is not a trade decision: gamma-trading expectations, borrow depth and
recall risk, issuer call and soft-call provisions, takeover protection, and the CB's own
liquidity all sit outside this module.
