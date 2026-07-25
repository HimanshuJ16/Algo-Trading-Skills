# Pre-Flight Checklist

- [ ] Is the new strategy firmly registered in the router under the `SHADOW` phase upon initial launch?
- [ ] Have you verified the `canary_scale_factor` is not aggressively set (> 10%)?
- [ ] Does the router handle rounding properly if the scaled quantity falls below the minimum exchange lot size (e.g., cancelling the order or forcing exactly 1 lot)?
- [ ] Are shadow trades properly isolated in the database to prevent polluting live PnL reporting?
