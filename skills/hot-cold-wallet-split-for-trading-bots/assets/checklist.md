# Pre-Flight Checklist

## Inputs
- [ ] Are Hot, Cold, and Warm balances all marked to USD at the **same** timestamp?
- [ ] Are all balances finite and non-negative (the engine raises `HotColdWalletError` otherwise)?
- [ ] Are submitted-but-unsettled transfers supplied via `pending_transfer_to_cold_usd` / `pending_transfer_to_hot_usd`?

## API key
- [ ] Is `enableWithdrawals` disabled on the trading key?
- [ ] Is `enableInternalTransfer` disabled? (It moves funds even with withdrawals off.)
- [ ] Is `permitsUniversalTransfer` disabled? (Same.)
- [ ] Was `ipRestrict` actually checked, rather than left unassessed?
- [ ] Was the permission set read live from the exchange, not assumed from how the key was created?

## Policy band
- [ ] Does the band satisfy `min < target < max`?
- [ ] Are the thresholds calibrated to real working-capital needs, rather than left at the defaults? (15%/25%/5% have **no regulatory basis**.)
- [ ] If client assets are held under a mandated ceiling (HK 2%, Japan 5%, Korea 20%), is `regulatory_max_hot_ratio` set?

## Outputs
- [ ] Is `is_transfer_fully_fundable` checked before executing? (A capped proposal does not resolve the imbalance.)
- [ ] Is `CRITICAL_SECURITY_ALERT` routed to halt trading, not just logged?
- [ ] Is `REGULATORY_HOT_CAP_BREACH` escalated to compliance separately from an ordinary rebalance?
- [ ] Does the executor attach a client-side transfer ID and avoid retrying on timeout alone?
- [ ] Is the full report persisted, including both ratios, the threshold band, and `security_findings`?

## Out of scope — covered elsewhere
- [ ] Is per-venue counterparty exposure capped separately? (One aggregate ratio cannot see it.)
- [ ] Are key rotation, storage, and least-privilege reviewed on their own cadence?
