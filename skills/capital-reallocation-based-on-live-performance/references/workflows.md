# Workflows for Capital Reallocation

1. **Settlement**: At the reallocation boundary (EOD, weekly), aggregate each active
   strategy's **closed** trades over a fixed trailing window into win rate, average win
   magnitude and average loss magnitude. Exclude open positions — marking a strategy on
   unrealised PnL sizes it on a number that may reverse before it is booked.

2. **Sample-adequacy gate**: Before sizing, check the trailing window contains enough
   closed trades to estimate the win rate meaningfully
   ($\mathrm{SE}(W) = \sqrt{W(1-W)/n}$). If not, hold the strategy's current allocation
   rather than reallocating on noise. This gate is the caller's responsibility; the
   engine sizes whatever statistics it is given.

3. **Kelly sizing**: The engine computes $f^*_s = W_s - (1-W_s)/R_s$, floors it at zero,
   and multiplies by `kelly_fraction` to obtain each strategy's target exposure as a
   fraction of the fund. Strategies with no measurable edge target zero.

4. **Constraint resolution**:
   - Per-strategy ceiling: $\min(\text{max\_capacity}_s,\ \text{fund} \times f_s \times \text{kelly\_fraction})$.
   - Fund-level: if gross Kelly demand exceeds 1.0, all targets scale down proportionally
     — the engine never levers.
   - Capital freed by a capacity-capped strategy is redistributed among strategies still
     below their own Kelly target, and stops there. It is **not** used to fill the fund.
   - Whatever remains undeployed is held as cash and logged. There is no allocation floor:
     forcing a minimum into an edgeless strategy is over-betting, not diversification.

5. **Delta generation**: Compare `current_capital` against the resolved target and issue
   the difference to the OMS risk layer.

6. **Slow unwinding**: A negative delta is a reduction in *buying power*, not a
   liquidation instruction. Lower the strategy's risk limit, block new entries, and let
   natural trade exits release the capital. The engine emits targets; it never emits
   orders. The one case that looks abrupt is the all-zero-edge portfolio, where every
   target goes to zero — that is still a limit change, and the OMS decides the unwind
   pace.

7. **Correlation review**: The engine sizes strategies independently. On the same cadence,
   review the live correlation matrix; if strategies have converged onto the same
   exposure, reduce `kelly_fraction` or apply an external gross-exposure cap, because
   summed independent Kelly fractions overstate safe leverage for correlated bets.
