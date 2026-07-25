# Workflows for Early Exercise Decision Management

## Real-Time Options Assessment

1. **Market Data Feed**: The system ingests real-time options pricing (NBBO) and underlying equity prices.
2. **Dividend Schedule Check**: Every afternoon (prior to market close), the system checks the corporate action calendar for imminent ex-dividend dates.
3. **State Evaluation**: For every ITM American option in the portfolio, pass the `OptionState` into `EarlyExerciseEvaluator.evaluate()`.
4. **Execution**: 
   - If `should_exercise = False`, do nothing. The position is held.
   - If `should_exercise = True` (e.g., to capture a massive dividend), the system routes an irrevocable exercise notice to the clearing broker before the 5:30 PM EST cutoff.
