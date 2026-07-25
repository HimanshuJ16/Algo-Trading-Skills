# Pre-Flight Checklist

- [ ] Does the broker account explicitly have Portfolio Margining enabled? (If Isolated, this code will dangerously under-report required margin).
- [ ] Is the correlation matrix updated daily using EWMA to capture recent volatility?
- [ ] Are extreme tail-risk correlation breakdowns (e.g., March 2020) factored into the margin haircut?
- [ ] Are positions across different exchanges incorrectly being pooled into the same cross-margin calculation? (They must be isolated per broker).