# VIX Strategy Sign-Off Checklist

Gates to clear before a VIX futures or options position goes live. Items marked
**[house]** are policy choices your firm must set; nothing on this list is an
exchange or regulatory requirement except where a specification is cited.

## Contract specification

- [ ] **Multipliers are not transposed.** VX futures settle at **$1,000** per index
      point; VIX **options** settle at **$100** per index point. Confirm every
      option figure in the sizing path uses `VIX_OPTIONS_MULTIPLIER`, not the
      futures constant. Transposing them is a 10x error in premium, payoff and
      budget consumption.
- [ ] **Settlement date resolved from the calendar, not inferred.** Monthly VX
      settles the Wednesday 30 days prior to the third Friday of the following
      month, moved back one business day for a Cboe Options holiday on that
      Wednesday or the Friday 30 days after it.
- [ ] **Weeklys checked.** If VIX Weeklys are listed, confirm "front contract" was
      selected by settlement date and not by month label — the wrong selection
      changes the sign of the curve slope.

## Market data and classification

- [ ] **Spot and both futures are live and finite.** NaN clears a naive `<= 0`
      guard; confirm the finiteness check is in the path.
- [ ] **F2 expires strictly after F1.** Reversed contracts invert the slope and
      flip the recommendation from short volatility to long volatility.
- [ ] **Thresholds are recorded as a decision.** **[house]** The ±2.0% dead band is
      a configurable default with no external authority behind it. Record who set
      it and against what.
- [ ] **Slope and basis are read as separate quantities.** A backwardated curve can
      carry a negative basis; neither can be inferred from the other.
- [ ] **The carry number carries its assumption.** `annualized_roll_yield_pct`
      assumes spot VIX is unchanged at settlement. It is not an expected return and
      must not be presented to a stakeholder as one.

## Position sizing

- [ ] **Contract counts are floored, never rounded up.** A budget that cannot fund
      one lot sizes to zero. Confirm no `max(1, ...)` survives anywhere in the
      sizing path.
- [ ] **Futures sized on notional; options sized on premium.** Premium is the loss
      bound of a debit spread; futures notional is neither a loss bound nor a
      margin figure.
- [ ] **Notional exposure limit set and checked.** **[house]** Default 5% of
      equity. Confirm the realised `notional_exposure_usd` sits inside it.
- [ ] **Premium budget set and checked.** **[house]** Default 2% of equity.
- [ ] **Margin verified separately with the FCM.** Initial margin on short VX is set
      by the clearing member and moves with volatility. Notional budget is not
      margin budget.

## Options pricing

- [ ] **Priced off the VX future, not spot VIX.** The option and the future settle
      to the same SOQ; the future is the tradeable forward.
- [ ] **Per-strike implied volatility used.** VIX smiles slope upward in strike;
      an ATM quote misprices a far OTM call. No ATM fallback is acceptable.
- [ ] **Net debit is priced or quoted, never assumed.** A fraction-of-width rule of
      thumb is not a market observable.
- [ ] **Max profit is reported net of the debit** — `(width − debit) × $100`, not
      the gross width.
- [ ] **Breakeven stated against the SOQ**, the value the contract actually settles
      to.

## Risk controls and exit

- [ ] **Protective stop is a resting order, not an intention.** Enter the buy-stop
      in the same operation as the short, referenced to **F1's own price**, not to
      spot VIX.
- [ ] **`loss_at_stop_usd` compared against the drawdown limit** before the trade,
      and against realised loss after any stop fill.
- [ ] **Slippage assumed, not ignored.** Volatility spikes gap through resting
      levels; the stop level is not the loss bound.
- [ ] **Out-of-band kill switch armed** for any short-volatility position — see
      `kill-switch-and-drawdown-circuit-breakers`. A short VX future has unbounded
      loss.
- [ ] **Roll trigger set on days-to-expiry** **[house]**, and the roll re-runs
      classification and re-sizes against the new front two rather than carrying
      the old contract count forward.
- [ ] **Old stop orders cancelled on roll.** A stop attached to a closed contract
      protects nothing.
