---
name: liquidity-adjusted-position-sizing
description: Use when calculating portfolio position sizes to cap allocations by Average
  Daily Volume (ADV) and order book depth, enforcing Days-to-Liquidate (DTL) limits
  and preventing market impact lockup.
domain: algorithmic-trading
subdomain: risk-management
tags:
- risk-management
- position-sizing
- liquidity-adjustment
- adv-cap
- days-to-liquidate
- market-impact
brokers_frameworks:
- Liquidity Position Sizer Engine
- Python NumPy
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when sizing positions across a universe with mixed liquidity profiles (large-caps, micro-caps, thinly traded options/futures). A fixed-fractional rule ($5\%$ of NAV) sizes on the portfolio and ignores the instrument: the same $\$500{,}000$ is trivial in a mega-cap and a multi-week exit in a micro-cap. This skill caps the position at the size the instrument can actually absorb, measured as Days-to-Liquidate (DTL) at a bounded participation rate and, optionally, as a multiple of displayed book depth.

The output is a **size**, and it belongs on the pre-trade path, downstream of the strategy's target and upstream of the order router.

## When NOT to Use

- **As a market-impact or cost model.** It returns shares, never a predicted cost. Impact estimation belongs in `transaction-cost-analysis-tca-integration`.
- **As portfolio construction.** It sizes one instrument at a time. Ten independently liquidity-capped positions in the same crowded factor still exit through one door — see `correlation-aware-exposure-limits` and `concentration-risk-single-name-limits`.
- **When the binding constraint is volatility rather than liquidity** — see `dynamic-position-sizing-based-on-realized-volatility`. The two caps compose (take the tighter); neither substitutes for the other.
- **As the execution schedule.** A DTL of 3 days is a statement about capacity, not an instruction to work the order over 3 days. Scheduling is `execution-algo-twap-vwap-slicing` and `participation-of-volume-pov-execution`.
- **On an ADV you have not sanity-checked.** ADV is an input, not a forecast; see the first pitfall below.

## Prerequisites

- Target capital allocation $V_{\text{target}}$, in the currency of $S$. May be negative for a short — the magnitude is capped and the sign preserved.
- Price $S$ **per unit of the ADV series**. For futures and options that is the price per *contract* (quote $\times$ multiplier), with ADV in contracts; a raw quote paired with contract volume oversizes the position by the multiplier.
- 20-day Average Daily Volume $\text{ADV}_{20d}$ in shares/contracts, strictly positive and finite.
- Max participation rate $\alpha$ (policy default $10\%$) and max DTL $DTL_{\text{max}}$ (policy default $1.0$ day). These are internal policy, not regulatory limits — see `references/standards.md` for what is actually regulator-set.
- Optional: a snapshot of reachable book depth in shares, if the depth cap is to be applied.

## Workflow

1. **Compute the daily liquidity capacity and the size cap**:
   $$\text{DailyCapacity} = \frac{\alpha}{100} \times \text{ADV}_{20d}, \qquad \text{MaxShares}_{\text{adv}} = \text{DailyCapacity} \times DTL_{\text{max}}$$
   - **Decision point — the real control is $\alpha \times DTL_{\text{max}}$, a cap on size relative to ADV.** Metaorder impact follows a square-root law in total size over daily volume and is, to a first approximation, insensitive to the participation rate and to how long the order is worked (Tóth et al. 2011). Raising $DTL_{\text{max}}$ to fit a bigger position does not make that position cheaper to trade — it only lengthens the exit. Widen it because you accept a longer liquidation horizon, never because you want less impact.

2. **Apply the optional book-depth cap**:
   $$\text{MaxShares}_{\text{depth}} = m \times \text{BookDepth}_{\text{shares}}$$
   - **Decision point — use it when ADV and depth disagree.** ADV inflated by a handful of block prints or one index rebalance describes volume that will not be there on the day you exit. Depth is the cross-check. It is a *snapshot* that can be pulled, so it tightens the ADV cap, never replaces it. There is no standard value for $m$; calibrate it from your own execution data.

3. **Cap the magnitude, preserve the side, floor to whole units**:
   $$\text{FinalShares} = \operatorname{sign}(V_{\text{target}}) \times \left\lfloor \min\left(\left|\frac{V_{\text{target}}}{S}\right|,\ \text{MaxShares}_{\text{adv}},\ \text{MaxShares}_{\text{depth}}\right) \right\rfloor$$
   - **Decision point — a short is capped exactly like the mirror long.** Covering a short in a name that cannot absorb the flow is at least as hard as selling the long, and worse in the tail. Never let a negative target take an unbounded path.
   - Share counts are **floored, never rounded**. A limit may only be approached from below; rounding a cap of $9{,}999.999$ up to $10{,}000$ breaches the limit it exists to enforce.

4. **Handle the degenerate outcomes explicitly**:
   - **Decision point — a cap that floors to zero is an answer, not an error.** If $\text{MaxShares} < 1$, the instrument admits no position under this policy. Drop it or widen the policy deliberately; do not round up to one lot.
   - Non-finite or non-positive inputs must **raise**, never size. MiFID II RTS 6 Art. 15 requires pre-trade volume limits to act as hard blocks, and a limit that cannot evaluate its inputs has not blocked anything.

5. **Emit the liquidity scaling audit**: record target and final shares, both DTL figures, the scaling factor, and `binding_constraint` — which of the two ceilings actually bound. A capped result whose audit line does not say *why* cannot be reviewed later.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating ADV as a forecast.** A 20-day mean spanning a holiday stretch, an expiry, or one index-rebalance print overstates the volume that will be available on the day you need to exit — and the exit that matters is the stressed one, when volume is lowest and everyone in the crowded trade is leaving at once. Feed a stressed or haircut ADV; the sizer cannot detect an optimistic one.
- **Reading a NaN as a pass.** Every comparison against NaN is False, so a NaN price or ADV slips through both `price <= 0` and `target > cap` and takes the *uncapped* branch. The pre-2.0.0 engine returned a NaN share count labelled "Liquidity Sizing OK" with a scaling factor of $1.0$. Reject non-finite inputs at the boundary — a sizer that emits NaN on corrupt reference data is worse than none, because the caller has been told the position passed.
- **Letting shorts bypass the cap.** `-50,000 > 10,000` is False, so a signed target compared directly against a positive cap is never constrained. Cap the magnitude.
- **A misconfigured limit inverting the trade.** An unvalidated negative $\alpha$ produces a negative cap and returns a short where a long was requested. Validate the policy at construction, not at first use.
- **Rounding a risk cap to two decimals.** `round(9_999.999, 2)` is `10_000.0` — one share past the limit. Floor.
- **Reporting only the pre-cap DTL.** The requested position's DTL and the returned position's DTL are different numbers; showing the first next to a message asserting the limit was met makes the audit trail contradict itself. Report both.
- **Sizing derivatives on the raw quote.** ADV in contracts with price per share oversizes by the contract multiplier — precisely on the thinly traded instruments this skill exists to protect.
- **Confusing the participation cap with the execution schedule.** The cap says the position is exitable in $DTL_{\text{max}}$ sessions at $\alpha$; it does not commit the router to trading at $\alpha$, and the router trading faster silently invalidates the assumption behind the cap.

## Verification

Run `python -m unittest discover -s skills/liquidity-adjusted-position-sizing/scripts` and confirm a 100% pass rate. The suite pins the behaviour below.

- **Cap binds.** `LiquidityPositionSizer(max_participation_pct=10.0, max_dtl_days=1.0)` with `calculate_size("ILLIQ", 500_000.0, price=10.0, adv_shares_20d=100_000.0)`: capacity is $0.10 \times 100{,}000 = 10{,}000$ shares/day, so the cap is $10{,}000$ shares. Verify `liquidity_capped_shares == 10_000.0`, `liquidity_capped_capital_usd == 100_000.0`, `scaling_factor == 0.2`, `dtl_days_target == 5.0`, `dtl_days_final == 1.0`, `binding_constraint == "adv_dtl"`.
- **Short is symmetric.** The same call with `-500_000.0` returns `-10_000.0` shares and `is_liquidity_constrained` true.
- **Depth binds.** With `adv_shares_20d=10_000_000.0` (ADV cap $1{,}000{,}000$ shares) and `book_depth_shares=5_000.0` at `max_book_depth_multiple=1.0`, verify the result is $5{,}000$ shares with `binding_constraint == "book_depth"`.
- **Floor, not round.** With `adv_shares_20d=99_999.99` the cap is $9{,}999.999$ shares; verify the result is $9{,}999$, not $10{,}000$.
- **At the limit is allowed.** A request of exactly $10{,}000$ shares against a $10{,}000$-share cap is not constrained; $10{,}001$ is.
- **Negative checks.** NaN or $\pm\infty$ in any of target/price/ADV/depth, a non-positive price/ADV/depth, a numeric *string*, a blank symbol, $\alpha \le 0$ or $\alpha > 100$, and a non-positive `max_dtl_days` or `max_book_depth_multiple` must each raise `ValueError`.

## Related Skills

- `correlation-aware-exposure-limits`
- `transaction-cost-analysis-tca-integration`
- `concentration-risk-single-name-limits`
- `dynamic-position-sizing-based-on-realized-volatility`
- `strategy-capacity-estimation-before-scaling-capital`
- `minimum-fill-size-and-lot-rounding-logic`
- `participation-of-volume-pov-execution`
