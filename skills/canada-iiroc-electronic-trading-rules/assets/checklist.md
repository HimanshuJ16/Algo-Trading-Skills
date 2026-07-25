# Pre-Flight Checklist

- [ ] Is the Pre-Trade Risk Engine positioned synchronously before the FIX router?
- [ ] Are price collars dynamically linked to the reference price (LTP or Previous Close)?
- [ ] Does the engine actively block Sell orders missing a Short flag if the inventory is zero?
- [ ] Are all rejected orders logged persistently for the Head of Trading to review?