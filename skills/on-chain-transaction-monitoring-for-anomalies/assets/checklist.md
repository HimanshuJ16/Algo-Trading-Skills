# Pre-Flight Checklist

- [ ] Is the sanctions snapshot non-empty, and is `sanctions_list_updated_at` set to the actual pull time (not "now")?
- [ ] Is the snapshot refresh cadence shorter than `max_sanctions_list_age_seconds`, and is a `SANCTIONS_LIST_STALE` flag treated as an operational incident rather than noise?
- [ ] Is it understood that a stale snapshot fails in **both** directions — missing new designations and blocking addresses that have been delisted (100+ Tornado Cash addresses were removed on 21 Mar 2025)?
- [ ] Is a clean verdict read as "no listed-address hit" rather than "sanctions-clear"? (OFAC's address listings are not likely to be exhaustive — FAQ 646.)
- [ ] Does a listed-address match route to compliance with `matched_sanctioned_addresses` and the snapshot as-of time, and is it understood that not broadcasting is **not** an OFAC blocking of property nor the 31 CFR 501.603(b)(1) report?
- [ ] Is `gas_price_gwei` the **effective** gas price ($\text{baseFee} + \min(\text{maxPriorityFee}, \text{maxFee}-\text{baseFee})$), not `maxFeePerGas`?
- [ ] Is `gas_baseline_gwei` configured so the relative ($5\times$ baseline) rule is actually enforced, rather than relying on the mainnet-shaped 200 Gwei ceiling alone?
- [ ] Are the fixed thresholds (\$50,000, 200 Gwei, 24h list age) calibrated for **this** chain and this wallet's normal traffic, rather than left at the shipped defaults?
- [ ] Does the calldata decoder emit `UNKNOWN_METHOD_SIGNATURE` for unresolved selectors and `NATIVE_TRANSFER_SIGNATURE` only for genuinely empty calldata — never a blank string?
- [ ] Are whitelisted method signatures in canonical form (`transfer(address,uint256)`, no spaces) and correctly cased, given the selector is keccak-256 of the exact signature?
- [ ] Are approval-granting methods (`approve`, `setApprovalForAll`, `increaseAllowance`) deliberately absent from the whitelist **and** present in `blocking_methods`? Absence from the whitelist alone scores only 30 and does not block, and an approval moves no value for the high-value vector to catch.
- [ ] Is `OnChainMonitoringError` handled as a data incident that halts the transaction, never swallowed into a "no flags, therefore safe" path?
- [ ] Is `ANOMALY_SUSPECTED` (score 30–69) wired to a real hold/review queue, given `is_blocked` is `False` in that band?
- [ ] Is it understood that a non-sanctions combination (high value + unapproved method, or stale list + high value) reaches exactly 70 and blocks on its own?
- [ ] Is cumulative withdrawal velocity enforced elsewhere, given this engine scores each transaction in isolation and cannot see structuring?
- [ ] Are only EVM addresses routed through this engine (Base58/Bech32 chains are case-sensitive and out of scope)?
- [ ] Are reports persisted for clean transactions too, as the evidence that screening ran and against which snapshot?
