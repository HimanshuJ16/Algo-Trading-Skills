# Workflows for On-Chain Transaction Monitoring for Anomalies

## 1. Sanctions Snapshot Preparation

1. Pull the sanctions list from its source of record and record the pull time as
   `sanctions_list_updated_at` (Unix seconds UTC). An undated list cannot be aged, and the
   policy constructor rejects it.
2. Normalize entries to lowercase hex for EVM chains; keep non-EVM addresses out of this
   policy entirely (Base58/Bech32 are case-sensitive).
3. Build a **new** `OnChainRiskPolicy` on every refresh. The address and method collections
   are frozen at construction, so a refreshed list cannot be smuggled in by mutating a live
   policy — and a refreshed list must carry a new timestamp anyway.
4. If screening must be turned off (testnet rig, offline replay), set
   `sanctions_screening_enabled=False` explicitly. Every report then carries
   `sanctions_screening_performed=False` and a disclaimer in `audit_notes`.

## 2. Transaction Payload Ingestion

1. Resolve the effective gas price. For an EIP-1559 (type-2) transaction:
   $\text{effective} = \text{baseFee} + \min(\text{maxPriorityFee},\ \text{maxFee} - \text{baseFee})$.
   Do **not** pass `maxFeePerGas` — it is a ceiling with a refund and over-flags routine traffic.
2. Decide which signatures are categorically prohibited (`blocking_methods`; approval-granting
   calls such as `setApprovalForAll(address,bool)`, `approve(address,uint256)` and
   `increaseAllowance(address,uint256)` are the usual candidates for a custody wallet). A
   signature cannot be both whitelisted and blocking.
3. Decode the calldata to a canonical Solidity signature (no spaces). If the selector cannot be
   resolved, pass `UNKNOWN_METHOD_SIGNATURE`; if calldata is genuinely empty, pass
   `NATIVE_TRANSFER_SIGNATURE`. Never pass a blank string.
4. Construct `OnChainTxPayload`. Validation is fail-closed: non-finite or negative `value_usd` /
   `gas_price_gwei`, a blank `tx_hash` or address, a negative or non-integer `block_number`, and a
   blank `method_signature` all raise `OnChainMonitoringError`. Handle that exception as a data
   incident — do not fall through to "no flags raised, therefore safe".

## 3. Multi-Vector Risk Audit

| Vector | Trigger | Penalty | Severity |
|---|---|---|---|
| `BLACKLIST_INTERACTION` | `from` or `to` (trimmed, lower-cased) is in the snapshot | +80 | CRITICAL |
| `SANCTIONS_LIST_STALE` | snapshot age at `tx.timestamp_utc` exceeds `max_sanctions_list_age_seconds` | +30 | HIGH |
| `HIGH_VALUE_SPIKE` | `value_usd > max_transfer_usd` | +40 | HIGH |
| `GAS_SPIKE_MEV` | `gas_price_gwei` above the fixed ceiling **or** above `gas_baseline_multiple × gas_baseline_gwei` | +20 (once) | MEDIUM |
| `UNAPPROVED_METHOD_CALL` | signature not whitelisted, or `UNKNOWN_METHOD_SIGNATURE` | +30 | HIGH |
| `BLOCKING_METHOD_CALL` | signature is in `blocking_methods` | +100 (forces a block) | CRITICAL |

All comparisons are strictly greater-than: a value exactly at a threshold does not flag.
The gas vector emits one flag and one penalty even when both gas rules trigger.
A negative list age (snapshot newer than the transaction — historical replay) is not staleness.

## 4. Risk Scoring & Block Action

1. Sum the penalties and cap at 100.
2. **A listed-address match, or a `blocking_methods` call, forces `HIGH_RISK_BLOCK`** regardless
   of the arithmetic, so re-tuning penalties can never silently unblock either. Use
   `blocking_methods` for approval-granting calls: they move no value, so the high-value vector
   cannot see them, and +30 alone leaves a drainer approval at `ANOMALY_SUSPECTED`.
3. Otherwise: $\ge 70 \Rightarrow$ `HIGH_RISK_BLOCK` (`is_blocked=True`);
   $30 \le s < 70 \Rightarrow$ `ANOMALY_SUSPECTED` (`is_blocked=False`, caller owns the hold
   decision); $s < 30 \Rightarrow$ `TRANSACTION_SAFE`.
4. Note the non-sanctions block paths: high value (40) + unapproved method (30) = 70, and
   stale list (30) + high value (40) = 70. Both block with no sanctions hit. Read the flags.

## 5. Audit Report Generation & Escalation

1. `OnChainMonitoringReport` carries `matched_sanctioned_addresses`,
   `sanctions_screening_performed`, `sanctions_list_updated_at` and `sanctions_list_age_seconds`
   alongside the score, flags and verdict.
2. On a listed-address match, escalate to compliance with that evidence. `is_blocked=True` means
   "not broadcast"; blocking the property and filing the initial report within 10 business days
   (31 CFR 501.603(b)(1)) are downstream obligations this engine does not perform.
3. Persist reports for both blocked and clean transactions — a clean verdict is the evidence that
   screening ran, and `sanctions_list_age_seconds` is the evidence of what it ran against.
4. The engine is stateless and deterministic (its only clock is `tx.timestamp_utc`), so a stored
   payload replays to an identical verdict against the same policy.
