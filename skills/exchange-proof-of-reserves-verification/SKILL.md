---
name: exchange-proof-of-reserves-verification
description: >-
  Use when deciding how much capital may sit on an exchange that publishes a Merkle sum
  tree proof of reserves; rehashes your inclusion path to the declared root and audits
  the branch for negative balances.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: crypto-custody-security
  tags: proof-of-reserves, merkle-sum-tree, on-chain-audit, crypto-custody, solvency-verification, counterparty-risk, sha256
  brokers_frameworks: "Binance zkPoR; Kraken Proof of Reserves; RFC 6962 Merkle Hash Trees; PCAOB Investor Advisory 2023-03-08; Python Decimal"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when deciding how much trading capital may sit on a centralised exchange, and the exchange publishes a cryptographic Proof of Reserves (PoR) you are being invited to trust. It turns a published Merkle sum tree root, your own audit path, the exchange's declared liability total and an independently established on-chain reserve figure into an auditable verdict rather than a screenshot of a dashboard.

The engine answers four separable questions, and reports them separately because they fail separately:

1. Is your balance really committed to by the published root?
2. Is any balance on your branch negative — the manipulation that shrinks declared liabilities?
3. Does the sum the tree commits to at its root equal the liability total the exchange published?
4. Do on-chain reserves cover that verified liability figure?

Question 3 is the one most PoR reviews skip, and it is the one that makes a Merkle *sum* tree worth more than a plain Merkle tree: without it an exchange can publish a correct tree alongside a smaller liability number and report an inflated coverage ratio that every individual inclusion proof will still pass.

## When NOT to Use

- **As evidence about the whole tree.** One inclusion proof is evidence about one branch. Binance's own account of why it moved to zk-SNARKs states that a Merkle inclusion proof cannot verify that all balances sum correctly or that no negative balances exist elsewhere in the tree. Whole-tree assurance needs a zero-knowledge circuit (Binance zkPoR) or a full leaf dump, neither of which this engine consumes.
- **As proof that the reserves exist and are unencumbered.** The engine takes `total_verified_onchain_reserves` as an input. Address attribution, signed control messages, and confirming the coins are not pledged, borrowed, or shuttled between venues for the snapshot are out of scope — and are where PoR exercises actually fail.
- **As an audit.** The PCAOB Office of the Investor Advocate advisory of **2023-03-08** states that PoR engagements "are not audits and, consequently, the related reports do not provide any meaningful assurance," provide "no assurance regarding the effectiveness of internal controls," and are not subject to PCAOB standards or inspection.
- **On a margin venue's per-asset tree without adjusting the negative-balance rule.** Binance's zkPoR constraint is that a user's *total net* balance is non-negative. A user who has borrowed BTC legitimately holds a negative per-asset BTC balance, so a per-asset tree that forbids negative leaves models a spot-only book.
- **As a substitute for position limits.** A verified 105% snapshot from last quarter does not bound today's exposure. See `counterparty-and-broker-concentration-risk`.

## Prerequisites

- The exchange's declared Merkle root for the asset, and the **snapshot timestamp** it refers to.
- Your leaf (`account_id`, `asset_symbol`, `balance`) and audit path exactly as published, with each sibling's hash, balance and side.
- The exchange's declared total user liabilities for that asset, denominated in the same asset as the leaf.
- An **independently established** on-chain reserve figure for that asset — this skill does not derive it.
- The exchange's balance precision and preimage encoding. `balance_decimals` must match the precision used to build the tree, and the engine's own encoding is self-consistent, not universal: match the exchange's byte layout before comparing against its published root.

## Workflow

1. **Fix the snapshot and the denomination.** Record which timestamp the root refers to and which asset the tree covers. Liabilities and reserves must both be in `user_leaf.asset_symbol`; the engine verifies one asset at a time and cannot detect a mismatched pair.
2. **Canonicalise before hashing, never after.** Balances are converted to fixed-point `Decimal` at `balance_decimals`. Do not pass binary floats for large books: a 25-billion-unit stablecoin liability carries more significant digits than a float holds, and float sums are order-dependent, so a float verifier cannot reproduce the exchange's root or detect a small shortfall.
3. **Rehash the branch, auditing every balance on it.** The traversal runs to completion even after finding a negative node — an operator investigating manipulation still needs to know whether the root matched. `is_sibling_right=True` means the sibling is the right child; getting the side wrong produces `INVALID_MERKLE_PROOF`, not a silent pass.
4. **Compare the root sum to the declared total — but only once the root hash matches.** If the hash mismatched, the recomputed sum is a sum over nodes nothing has authenticated, so the engine records `ROOT_SUM_UNVERIFIABLE` rather than a spurious liability finding. If the hash matched and the sums differ, the verdict is `INCONSISTENT_LIABILITY_TOTAL`: the ratio is computed from a denominator you have just disproved, so do not act on it. Only set `enforce_root_sum_match=False` for a genuine plain Merkle tree; the report then carries `ROOT_SUM_UNENFORCED` to record that liabilities were taken on trust.
5. **Decide on the exact ratio, report a truncated one.** The verdict compares the unrounded ratio against `min_reserve_ratio_pct`; the reported figure is truncated downward so a published number can never overstate coverage. Rounding before comparing turns a 99.999% deficit — 0.5 BTC on a 10,000 BTC book — into a clean pass.
6. **Re-run on a cadence and diff.** A single snapshot is a point estimate. Track the root, the root sum and the ratio across publications; a liability total that falls while user counts rise is the signal, not any one verdict.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Verifying inclusion and stopping there.** An inclusion proof shows your balance is in the tree. It says nothing about the total. Unless the root sum is checked against the declared liabilities, an exchange publishes a valid tree, declares a lower liability figure, and every user's proof still verifies while the reported reserve ratio is fiction.
- **Rounding the ratio before comparing it to the threshold.** `round(99.999, 2) == 100.0`. A verifier that rounds first and compares second certifies an insolvent book as fully reserved, and the audit note it prints will read "100.00%" beside the word SOLVENT.
- **Hashing formatted floats.** `0.1 + 0.2` is not `0.3` in binary floating point, and no float can hold `25000000000.00000001`. Both the hash preimage and the sum must come from exact decimal arithmetic, or the verifier silently disagrees with the exchange about what the tree contains.
- **Omitting leaf/interior domain separation.** RFC 6962 §2.1 requires distinct hash prefixes for leaves and interior nodes "to give second preimage resistance." Under a naive delimiter-joined encoding an attacker-chosen `account_id` makes a leaf preimage byte-identical to an interior node, letting an entire subtree be presented as one small user leaf.
- **Case-sensitive root comparison.** Exchanges publish roots upper-case, lower-case and `0x`-prefixed. A raw string compare reports `INVALID_MERKLE_PROOF` for a correct proof — the error direction that teaches an operator to ignore the tool.
- **Letting a non-finite balance through the negative check.** Every comparison against NaN is False, so `balance < 0` does not catch NaN. It flows into the sum and the ratio and yields a verdict computed from nothing. Non-finite inputs must raise.
- **Reading `-0.0` as a negative balance.** A serialised negative zero is not a manipulated leaf. Canonicalise it to `0` before the sign test or the verifier cries wolf.
- **Treating on-chain control as unencumbered ownership.** Wallet balances do not show whether assets are pledged as DeFi collateral, borrowed for the snapshot, or shared between venues. Vitalik Buterin's 2022 write-up notes that shuttling collateral between exchanges "is something that exchanges could easily do, and would allow them to pretend to be solvent when they actually are not."
- **Accepting a stale snapshot.** A root from last quarter proves something about last quarter. Nothing in a PoR publication constrains what happened the day after.

## Verification

- Build a two-leaf tree (2.5 + 7.5 BTC), declare liabilities of 10 BTC and reserves of 10.5 BTC, and confirm `SOLVENT_FULL_RESERVES` at exactly `Decimal("105")` with an empty `findings` list. Drop reserves to 9.2 BTC and confirm `INSOLVENT_RESERVE_DEFICIT` at `Decimal("92")`.
- Keep the same tree but declare liabilities of 8 BTC against 9 BTC of reserves. Confirm `INCONSISTENT_LIABILITY_TOTAL`, `is_declared_liability_consistent is False`, and `computed_merkle_root_balance == Decimal("10")` — not a 112.5% pass.
- Set reserves to 9.9999 BTC against 10 BTC of liabilities and confirm `INSOLVENT_RESERVE_DEFICIT`, and that the audit note reads `99.999%` rather than `100.00%`.
- Build a tree summing to `25000000000.00000001` USDT, supply `25000000000` of reserves, and confirm the one-satoshi shortfall is detected — a float engine cannot distinguish these two numbers.
- Pass a leaf whose `account_id` is a node hash and whose `asset_symbol` embeds a formatted balance and a second hash, and confirm the leaf digest differs from the interior node digest it was crafted to forge.
- Submit `float("nan")`, `"inf"`, a negative reserve figure, a zero liability total, or a 63-character root and confirm `ProofOfReservesError` rather than a solvency verdict.
- Run `python -m unittest discover -s skills/exchange-proof-of-reserves-verification/scripts` and confirm a 100% pass rate.

## Related Skills

- `ftx-style-exchange-post-collapse-risk-lessons`
- `counterparty-and-broker-concentration-risk`
- `custody-solution-vendor-due-diligence-checklist`
- `third-party-custody-audit-report-review-cadence`
