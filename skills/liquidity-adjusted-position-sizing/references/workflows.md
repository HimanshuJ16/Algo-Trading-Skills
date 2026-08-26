# Deep Workflow Reference — liquidity-adjusted-position-sizing

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

0. **Validate the policy and the inputs before any arithmetic**:
   - Policy, at construction: $0 < \alpha \le 100$, $DTL_{\text{max}} > 0$, $m > 0$, all
     finite. An unvalidated negative $\alpha$ yields a *negative* cap and returns a
     short where a long was requested — a misconfigured risk limit inverting the trade.
   - Inputs, per call: price and ADV finite and strictly positive; target finite (it may
     be negative or zero); depth, if supplied, finite and strictly positive.
   - Reject non-finite values explicitly. Every comparison against `NaN` is False, so a
     `NaN` passes both `price <= 0` and `target > cap` and takes the *uncapped* branch,
     producing a `NaN` position reported as a pass.
   - Confirm price and ADV are in the **same units**: price per contract with ADV in
     contracts for derivatives. A raw quote against contract volume oversizes by the
     multiplier.

1. **Compute liquidity capacity**:
   $$\text{DailyCapacity} = \frac{\alpha}{100} \times \text{ADV}_{20d}$$
   Strictly positive once step 0 has run, so the Days-to-Liquidate division below needs
   no fabricated denominator floor.

2. **Compute the ADV/DTL size cap**:
   $$\text{MaxShares}_{\text{adv}} = \text{DailyCapacity} \times DTL_{\text{max}}$$
   This product — not $\alpha$ alone — is the control that bounds market impact, because
   impact scales with $Q/\text{ADV}$ and is roughly insensitive to how slowly $Q$ is
   worked (see `references/standards.md`).

3. **Compute the optional book-depth cap**:
   $$\text{MaxShares}_{\text{depth}} = m \times \text{BookDepth}_{\text{shares}}$$
   Apply it when ADV may be inflated by block prints or a rebalance. Depth is a
   snapshot that can be pulled and excludes hidden liquidity, so it tightens the ADV
   cap and never substitutes for it. Skip this step when no depth snapshot is supplied.

4. **Apply the binding cap to the magnitude, preserving the side**:
   $$\text{FinalShares} = \operatorname{sign}(V_{\text{target}}) \times \left\lfloor \min\left(\left|\frac{V_{\text{target}}}{S}\right|,\ \text{MaxShares}_{\text{adv}},\ \text{MaxShares}_{\text{depth}}\right) \right\rfloor$$
   - Compare **magnitudes**. A signed target compared against a positive cap never
     constrains a short, because $-50{,}000 > 10{,}000$ is False.
   - **Floor, never round.** `round(9_999.999, 2)` is `10_000.0`, one share above the
     cap. A limit may only be approached from below. Venue lot sizes are applied
     downstream by `minimum-fill-size-and-lot-rounding-logic`.
   - A cap that floors to zero means the instrument admits no position under this
     policy. Return zero and log it; never round up to one lot.

5. **Report both liquidation horizons**:
   $$DTL_{\text{target}} = \frac{|\text{TargetShares}|}{\text{DailyCapacity}}, \qquad DTL_{\text{final}} = \frac{|\text{FinalShares}|}{\text{DailyCapacity}}$$
   The requested position's DTL and the returned position's DTL are different numbers.
   A single field holding the first, next to a message asserting $DTL \le DTL_{\text{max}}$,
   makes the audit trail contradict itself.

6. **Emit the liquidity scaling audit**: target shares, final shares, both DTL figures,
   the scaling factor $|\text{Final}| / |\text{Target}|$, and `binding_constraint` —
   `adv_dtl`, `book_depth`, or `none`. A capped result whose audit line does not record
   which ceiling bound cannot be reviewed after the fact.

## Composition with other controls

Liquidity is one ceiling among several. Take the tightest of the volatility-target size
(`dynamic-position-sizing-based-on-realized-volatility`), the single-name concentration
cap (`concentration-risk-single-name-limits`), and this liquidity cap. They constrain
different failure modes and none subsumes another. The scaling factor here is a
*deployment ratio*, not a signal: a position scaled to 20% of target is a strategy that
does not fit the instrument, which is capacity information worth escalating — see
`strategy-capacity-estimation-before-scaling-capital`.

## Production Implementation Reference

- Reference code: `scripts/liquidity_position_sizer.py`
  (`LiquidityPositionSizer`, `LiquiditySizingResult`).
- Automated unit tests: `scripts/test_liquidity_position_sizer.py`, including regression
  tests for the uncapped-short, silent-NaN, round-past-the-cap and inverted-policy
  defects, and an invariant sweep over the parameter grid.
