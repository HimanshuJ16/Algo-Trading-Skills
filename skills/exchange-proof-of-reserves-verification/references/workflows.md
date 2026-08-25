# Workflows for Exchange Proof of Reserves Verification

## 0. Establish scope before touching a hash

- Record the **snapshot timestamp** the declared root refers to. Every conclusion
  below is scoped to that instant and to nothing after it.
- Record which **asset** the tree covers and confirm the declared liability total and
  the on-chain reserve figure are in that same asset. The engine verifies one asset
  at a time and cannot detect a mismatched pair.
- Record which **balance types** the tree covers (spot only, or spot plus margin,
  futures and staked). A proof that omits a liability class understates the
  denominator no matter how cleanly it verifies.
- Confirm the exchange's **balance precision** and set `balance_decimals` to match.
  A verifier configured for the wrong precision hashes a different preimage and can
  never reproduce the published root.

## 1. Canonicalise inputs

- Convert every balance to exact fixed-point `Decimal` at `balance_decimals`. Pass
  values as `str` or `Decimal`. `float` is accepted and routed through `str`, but a
  float cannot hold a large stablecoin total, and float summation is order-dependent.
- Reject non-finite values. `NaN < 0` is False, so an unchecked NaN passes the
  negative-balance guard and then poisons the sum and the ratio.
- Normalise `-0` to `0` so a serialised negative zero is not reported as
  manipulation.
- Normalise the declared root: strip whitespace and any `0x` prefix, lower-case it,
  and require 64 hex characters. Reject anything else rather than reporting a
  malformed root as a failed proof.

## 2. Recompute the branch

- Leaf digest: `SHA-256(0x00 ‖ framed(account_id) ‖ framed(asset_symbol) ‖ framed(balance))`.
- Interior digest: `SHA-256(0x01 ‖ framed(left_hash) ‖ framed(left_balance) ‖ framed(right_hash) ‖ framed(right_balance))`,
  parent balance `= left_balance + right_balance`.
- `framed(x)` prefixes each field with its 8-byte big-endian length, so no field's
  contents can forge a field boundary. The `0x00`/`0x01` prefixes are the RFC 6962
  §2.1 domain separation that gives second preimage resistance.
- `is_sibling_right=True` means the sibling is the right child and the running node
  is the left input. Mislabelling a side must break the proof, not silently pass.
- Walk the whole path even after a finding. An operator investigating a negative node
  still needs to know whether the root matched.
- An empty path is valid only for a single-leaf tree, where the root is the leaf
  digest.

## 3. Audit balances on the branch

- Flag any negative leaf or sibling balance. This is the check that defeats the
  fake-account manipulation: a concealed shortfall inserted as a negative balance
  makes the branch proofs of the users above it fail.
- The engine sees only the branch it was given. It cannot conclude anything about
  leaves it has not been shown — that requires a zk circuit over the whole tree or a
  full leaf dump.
- On a margin-enabled venue a negative *per-asset* balance can be legitimate (a user
  who borrowed the asset). The zkPoR constraint is on a user's *net* balance across
  assets. Adjust the rule, or scope the tree to a spot-only book.

## 4. Reconcile the root sum against declared liabilities

- **Only after the root hash matches.** Before that the recomputed sum is a sum over
  unauthenticated nodes; record `ROOT_SUM_UNVERIFIABLE` instead of a liability
  finding.
- Root sum `!=` declared total → `INCONSISTENT_LIABILITY_TOTAL`. The reserve ratio is
  computed from a denominator you have just disproved; do not act on it, and treat
  the delta as the headline finding.
- For a genuine plain Merkle tree that commits no sums, set
  `enforce_root_sum_match=False`. The report then carries `ROOT_SUM_UNENFORCED`,
  which is the honest label: liabilities were taken on trust.

## 5. Compute the reserve ratio

- `ratio = reserves / verified_liabilities * 100`, evaluated exactly.
- Compare the **unrounded** ratio against `min_reserve_ratio_pct`. Rounding first
  turns a 99.999% deficit into a pass.
- Report the ratio truncated downward, and render audit notes from that truncated
  value, so no published figure ever overstates coverage or contradicts its own
  verdict.
- `min_reserve_ratio_pct` is configurable. 100% is the definition of full reserves;
  raise it if the mandate requires a buffer.

## 6. Classify and act

| Verdict | Meaning | Action |
|---|---|---|
| `INVALID_MERKLE_PROOF` | Root mismatch, or a negative balance on the branch. | No solvency conclusion is available. Re-fetch the proof; if it reproduces, escalate — this is either a broken publication or manipulation. |
| `INCONSISTENT_LIABILITY_TOTAL` | Branch verified; declared liabilities contradict the committed root sum. | Treat the published ratio as unsupported. Withdraw-and-observe pending an explanation of the delta. |
| `INSOLVENT_RESERVE_DEFICIT` | Verified liabilities exceed reserves at the configured threshold. | Reduce exposure. The shortfall figure is in `findings`. |
| `SOLVENT_FULL_RESERVES` | All branch, sum and ratio checks passed at the snapshot. | Proceed within existing counterparty limits. This is not evidence the reserves are unencumbered or unborrowed. |

## 7. Re-run and diff

- Verify on every publication, not once. Persist the root, the root sum, the ratio
  and the snapshot timestamp.
- The signal is in the series: a declared liability total that falls while user
  activity rises, a root that stops changing, a ratio that hugs 100% at every
  snapshot, or reserves that appear only around publication dates (the
  collateral-shuttling pattern).
- Keep the verdict, findings and inputs together as the audit trail. A verdict
  without its snapshot timestamp is not evidence of anything.
