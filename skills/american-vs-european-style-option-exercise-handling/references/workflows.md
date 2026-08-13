# Workflows for Early Exercise Decision Management

## Real-Time Options Assessment

1. **Market Data Feed**: The system ingests real-time options pricing (NBBO) and underlying equity prices.
2. **Dividend Schedule Check**: Every afternoon (prior to market close), the system checks the corporate action calendar for imminent ex-dividend dates.
3. **State Evaluation**: For every ITM American option in the portfolio, construct an `OptionState` and pass it into `EarlyExerciseEvaluator.evaluate()`.
4. **Execution**:
   - If `should_exercise = False`, do nothing. The position is held.
   - If `should_exercise = True` (e.g., to capture a massive dividend or below-parity arbitrage), the system routes an irrevocable exercise notice to the clearing broker before the 5:30 PM EST cutoff.

## Edge Cases

- **Below-Parity Options**: If an option is trading below its intrinsic value (illiquid, wide spreads), selling captures less than exercising. The engine detects this and returns `should_exercise = True` regardless of dividend status.
- **Dividend == Time Value Boundary**: When the dividend exactly equals the remaining time value, the engine defaults to not exercising (requires strictly greater dividend). This preserves optionality.
- **European-Style Options**: This engine is for American-style options only. European options have no early-exercise decision — verify the option's exercise style before evaluation.
