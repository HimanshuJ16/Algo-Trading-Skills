---
name: on-chain-transaction-monitoring-for-anomalies
description: >-
  Use when screening live or pending EVM transactions from custody wallets before
  broadcast, checking sanctioned-address interaction, list staleness, high-value
  transfer spikes and abnormal gas patterns.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: crypto-custody-security
  tags: on-chain-monitoring, kyt-compliance, ofac-sanctions, anomaly-detection, gas-spikes, crypto-custody, defi-security
  brokers_frameworks: "EVM Blockchain RPC / Web3; OFAC SDN List; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when screening live or pending-mempool EVM transactions for crypto trading bots, institutional treasuries, or custody wallets before they are broadcast or co-signed. On-chain threats — wallet-drainer exploits, transfers to OFAC-listed addresses, unauthorized high-value withdrawals, abnormal priority-fee spikes (MEV/front-running), and unapproved contract calls such as `setApprovalForAll` — must be detected before the signature leaves the process, because an EVM transaction cannot be recalled once mined. The engine evaluates five risk vectors, computes a composite score ($0-100$), and returns a deterministic execution verdict.

## When NOT to Use

- **As an OFAC compliance program.** A listed-address match is a *screening hit*, not a compliance outcome. `is_blocked=True` means "do not broadcast"; it is not an OFAC blocking of property and does not discharge the initial blocking report due within 10 business days under 31 CFR 501.603(b)(1). Route hits to compliance with the emitted evidence.
- **As proof a transaction is sanctions-clear.** OFAC's published digital currency address listings "are not likely to be exhaustive" (OFAC FAQ 646) — property of a blocked person is blocked whether or not its address is listed. A clean verdict means "no listed-address hit", nothing more. Chain-analytics tracing of indirect exposure is a separate control.
- **As a point-in-time historical compliance tool.** Screening is performed against the snapshot you supply, i.e. current designations. Listings move in both directions — over 100 Tornado Cash addresses were *removed* from the SDN List on 21 Mar 2025 — so replaying a 2023 transfer against a 2026 snapshot yields no hit even though the transfer was prohibited when it occurred.
- **On non-EVM chains.** Address comparison lower-cases both sides, which is correct only because EIP-55 checksummed and all-lowercase hex spell the same EVM account. Bitcoin/TRON Base58 and Bech32 addresses are case-sensitive; do not route them through this module.
- **As a withdrawal rate limiter.** This engine is stateless and scores one transaction in isolation. Cumulative velocity ("ten transfers of \$9,999 in an hour") is invisible to it — see `withdrawal-velocity-limits-and-anomaly-detection`.
- **With the shipped thresholds on a non-mainnet-Ethereum chain.** The 200 Gwei ceiling is Ethereum-mainnet-shaped and is meaningless where normal gas prices sit orders of magnitude away.

## Prerequisites

- Transaction payload (`tx_hash`, `from_address`, `to_address`, `value_usd`, `gas_price_gwei`, `method_signature`, `block_number`, `timestamp_utc`). All fields are validated; non-finite or negative numerics are rejected rather than screened.
- `gas_price_gwei` as the **effective** gas price. For an EIP-1559 (type-2) transaction that is $\text{baseFee} + \min(\text{maxPriorityFee}, \text{maxFee} - \text{baseFee})$, not `maxFeePerGas`.
- `method_signature` as the canonical Solidity signature (no spaces), or `NATIVE_TRANSFER_SIGNATURE` for empty calldata, or `UNKNOWN_METHOD_SIGNATURE` for calldata that could not be decoded. Never blank.
- A sanctions-list snapshot **and** its `sanctions_list_updated_at` timestamp (Unix seconds UTC). Screening cannot be enabled against an empty or undated list.
- Risk policy (`max_transfer_usd`, `max_gas_gwei`, optional `gas_baseline_gwei`, `whitelisted_methods`, optional `blocking_methods`).

## Workflow

1. **Validate the payload before scoring it.** Non-finite and negative numerics are rejected, not screened. A `NaN` `value_usd` or `gas_price_gwei` defeats every `>` comparison in the model, so an unvalidated corrupt feed scores $0$ and returns `TRANSACTION_SAFE` — the safest possible verdict produced by the worst possible data.
2. **Multi-Vector Risk Audit**:
   - **Vector 1 — Sanctions / listed-address interaction** (`from` or `to` matches the snapshot $\implies +80$). Both sides are trimmed and lower-cased first; method signatures are *not* lower-cased (see Pitfalls).
   - **Vector 2 — Sanctions-list staleness** (snapshot age at `tx.timestamp_utc` exceeds `max_sanctions_list_age_seconds` $\implies +30$). A negative age means the snapshot post-dates the transaction (historical replay) and is not a staleness condition.
   - **Vector 3 — High-value transfer** (`value_usd > max_transfer_usd` $\implies +40$).
   - **Vector 4 — Abnormal gas price** (`gas_price_gwei` above the fixed ceiling *or* above `gas_baseline_multiple` $\times$ `gas_baseline_gwei` $\implies +20$). One flag, one penalty: tripping both rules is not double-counted.
   - **Vector 5 — Unapproved contract method** (signature not in `whitelisted_methods`, or undecodable, $\implies +30$); a signature in `blocking_methods` instead forces a block outright.
   - **Decision point — an approval-granting call is a categorical risk, not an additive one.** `setApprovalForAll` moves no value, so Vector 3 cannot see the exposure it creates, and $+30$ alone does not reach the block threshold: a drainer approval scores $50$ with a gas spike and is merely `ANOMALY_SUSPECTED`. If this engine is relied on for drainer protection, put approval-granting signatures in `blocking_methods` (empty by default — which calls are prohibited is a custody policy decision, not one this module presumes).
   - **Decision point — undecodable calldata is the high-risk case, not the empty one.** A transaction whose selector could not be resolved (unverified contract, proxy) must be passed as `UNKNOWN_METHOD_SIGNATURE` and is always flagged; it can never be whitelisted. Only genuinely empty calldata (`NATIVE_TRANSFER_SIGNATURE`) skips this vector.
3. **Composite Risk Classification** (score capped at $100$):
   - A listed-address match, or a call to a `blocking_methods` signature, $\implies$ `HIGH_RISK_BLOCK` **regardless of the score arithmetic** — neither risk scales with transaction value.
   - Score $\ge 70 \implies$ `HIGH_RISK_BLOCK`; $30 \le \text{Score} < 70 \implies$ `ANOMALY_SUSPECTED`; Score $< 30 \implies$ `TRANSACTION_SAFE`.
   - **Decision point — the additive model blocks on non-sanctions grounds too.** A high-value transfer ($40$) calling an unapproved method ($30$) reaches exactly $70$ and blocks with no sanctions hit at all. A stale list ($30$) plus a high-value transfer ($40$) does the same. Read the flags, not just the status.
   - **Decision point — `ANOMALY_SUSPECTED` does not block.** `is_blocked` is `False` at scores $30$–$69$; the caller owns the hold/review decision for that band.
4. **Audit Report Generation**: emit `OnChainMonitoringReport` carrying `matched_sanctioned_addresses`, `sanctions_screening_performed`, `sanctions_list_updated_at` and `sanctions_list_age_seconds` — the evidence a downstream blocking report or investigation needs.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Screening against nothing and calling it safe**: a policy with an empty `sanctioned_addresses` set returns `TRANSACTION_SAFE` for every transaction while performing zero screening. The policy constructor rejects that combination; disabling screening requires the explicit `sanctions_screening_enabled=False`, which stamps every report so the verdict is never mistaken for a clearance.
- **Screening against a stale snapshot**: designations added since the pull are invisible, and — because listings are also *removed* — a stale snapshot keeps blocking transactions that are no longer prohibited. Age the list against the transaction timestamp, never leave it undated.
- **Confusing "blocked" with blocked property**: refusing to broadcast is an execution decision. Freezing the property of a designated person and filing the 31 CFR 501.603(b)(1) report within 10 business days are separate obligations this engine does not perform.
- **Lower-casing method signatures**: the 4-byte selector is keccak-256 of the *canonical* signature, so `transferFrom(address,address,uint256)` and its lowercased spelling are different functions. Addresses are case-insensitive; signatures are not.
- **Whitelisting a signature with a space in it**: `transfer(address, uint256)` is not the canonical form and would silently never match any real payload, quietly flagging every legitimate transfer. Whitelist entries containing whitespace are rejected at construction.
- **Screening `maxFeePerGas` against the gas ceiling**: wallets set `maxFeePerGas` to several times the base fee as headroom, and the refund makes the paid price much lower. Comparing the ceiling against the max fee manufactures MEV alerts on ordinary transactions.
- **A fixed Gwei ceiling as the only gas control**: 200 Gwei is an Ethereum-mainnet number and a normal-regime spike is relative, not absolute. Set `gas_baseline_gwei` so the $5\times$-baseline rule that `references/standards.md` describes is actually enforced.
- **Static screening only at onboarding**: address screening must run on every transaction against a current snapshot, not once at account opening.
- **Assuming an unapproved approval call blocks**: it does not, on the shipped weights. `setApprovalForAll(address,bool)` with a gas spike scores $50$ — flagged, not blocked — and the transfer that drains the wallet happens later, under the approval, as a transaction this engine never sees as unusual. Put approval-granting signatures in `blocking_methods`, and see `smart-contract-approval-scope-minimization`.
- **Treating a single-transaction verdict as a velocity control**: this engine has no memory between calls; structured withdrawals just under `max_transfer_usd` each score $0$.

## Verification

- Construct `OnChainRiskPolicy(sanctioned_addresses={...}, sanctions_list_updated_at=<unix_ts>)` and `OnChainAnomalyMonitorEngine(policy)`. Audit a standard transfer (\$1,000, 30 Gwei, whitelisted method, snapshot 100s old) $\implies$ `TRANSACTION_SAFE`, score $0$. Audit a transfer to a listed address $\implies$ `HIGH_RISK_BLOCK`, score $80$, with `matched_sanctioned_addresses` naming the matched address.
- Boundary checks: `value_usd` exactly `max_transfer_usd` and `gas_price_gwei` exactly `max_gas_gwei` must **not** flag (comparisons are strictly greater-than); list age exactly `max_sanctions_list_age_seconds` must not flag; score exactly $70$ blocks and exactly $30$ is `ANOMALY_SUSPECTED`.
- Fail-closed checks: `NaN`/`Inf`/negative `value_usd` or `gas_price_gwei`, a blank `method_signature`, a negative or non-integer `block_number`, an empty sanctions list with screening enabled, an undated list, and a whitelist entry containing whitespace must each raise `OnChainMonitoringError`.
- Case handling: a checksummed, whitespace-padded payload address must match a lowercase list entry, and vice versa; `Transfer(address,uint256)` must **not** match a `transfer(address,uint256)` whitelist entry.
- Run `python -m unittest discover -s skills/on-chain-transaction-monitoring-for-anomalies/scripts` and confirm a 100% pass rate.

## Related Skills

- `withdrawal-velocity-limits-and-anomaly-detection`
- `smart-contract-approval-scope-minimization`
- `sanctions-screening-for-counterparties-and-instruments`
- `exchange-withdrawal-whitelist-enforcement`
