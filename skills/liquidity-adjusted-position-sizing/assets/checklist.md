# Pre-Flight / Sign-off Checklist — liquidity-adjusted-position-sizing

## Input data
- [ ] ADV is computed over a stated window (conventionally 20 trading days) and the window is documented.
- [ ] ADV has been sanity-checked against holiday stretches, expiries, and single index-rebalance or block prints that inflate the mean.
- [ ] A stressed or haircut ADV is used where the exit being sized is a stressed exit.
- [ ] `price` and `adv_shares_20d` are in the **same units** — price per contract with ADV in contracts for futures and options.
- [ ] Non-finite (`NaN`/`inf`) and non-positive price/ADV/depth inputs are rejected, not sized.
- [ ] Book depth, if supplied, is a current snapshot and its staleness is bounded.

## Policy configuration
- [ ] Max participation $\alpha$ is validated at construction: finite and $0 < \alpha \le 100$.
- [ ] $DTL_{\text{max}}$ and the book-depth multiple $m$ are validated as finite and strictly positive.
- [ ] Defaults ($\alpha = 10\%$, $DTL_{\text{max}} = 1.0$, $m = 1.0$) have been calibrated and the rationale recorded — they are library defaults, not regulatory limits.
- [ ] It is understood that raising $DTL_{\text{max}}$ relaxes the size cap linearly and does **not** reduce market impact.
- [ ] Where a regulator-set participation limit does apply (e.g. EU/UK buy-back safe harbour, Delegated Regulation (EU) 2016/1052 Art. 3(3)), it is enforced separately and its current text has been re-checked.

## Sizing
- [ ] The cap binds on the **magnitude** of the request: a short is capped exactly as the mirror long.
- [ ] Share counts are floored, never rounded up above the cap.
- [ ] A cap that floors to zero returns zero and is surfaced — never rounded up to one lot.
- [ ] The book-depth cap is applied as a second ceiling where ADV and depth disagree, not as a replacement for the ADV cap.
- [ ] The cap is a hard pre-trade block, not an advisory warning (cf. MiFID II RTS 6 Art. 15 maximum order volumes).

## Audit
- [ ] Both $DTL_{\text{target}}$ and $DTL_{\text{final}}$ are recorded — the requested and returned positions have different liquidation horizons.
- [ ] `binding_constraint` (`adv_dtl` / `book_depth` / `none`) is recorded on every capped result.
- [ ] Scaling factor and target-vs-final share counts are persisted for post-trade review.
- [ ] Persistently large scale-downs are escalated as strategy capacity information, not silently absorbed.

## Scope
- [ ] Portfolio-level correlation and crowding are handled elsewhere; this sizer is per-instrument.
- [ ] Impact and cost estimation are handled elsewhere; this sizer returns a size only.
- [ ] Venue lot- and tick-size rounding is applied downstream.

## Testing
- [ ] Automated Testing: Run `python -m unittest discover -s skills/liquidity-adjusted-position-sizing/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
