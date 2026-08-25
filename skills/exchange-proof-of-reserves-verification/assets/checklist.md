# Pre-Flight Checklist — Exchange Proof of Reserves Verification

## Scope

- [ ] Snapshot timestamp of the declared root is recorded, and every conclusion below is labelled with it.
- [ ] Asset of the tree is recorded; declared liabilities and on-chain reserves are in that same asset.
- [ ] Balance types in scope are known (spot only, or spot + margin + futures + staked).
- [ ] `balance_decimals` matches the precision the exchange used to build the tree.
- [ ] The exchange's preimage encoding has been matched before comparing against its published root.

## Inputs

- [ ] Balances are supplied as `str`/`Decimal`, not binary floats, for any large book.
- [ ] Non-finite values (`NaN`, `inf`) raise rather than being compared.
- [ ] Declared root is normalised (whitespace, `0x` prefix, case) and is 64 hex characters.
- [ ] Declared liability total is > 0 and on-chain reserves are >= 0.

## Branch verification

- [ ] Audit path rehashes to the declared root, with sibling sides honoured.
- [ ] Leaf and interior digests use distinct domain-separation prefixes (RFC 6962 §2.1).
- [ ] No negative balance on the branch — and `-0` is not counted as negative.
- [ ] For a margin venue, the negative-balance rule has been scoped to net balance, not per-asset.

## Liability reconciliation

- [ ] Root sum is compared to the declared liability total — **only after** the root hash matches.
- [ ] Any delta is recorded as the headline finding, not absorbed into the ratio.
- [ ] `enforce_root_sum_match=False` is used only for a genuine plain Merkle tree, and the resulting `ROOT_SUM_UNENFORCED` finding is carried into the write-up.

## Solvency

- [ ] Verdict is taken on the unrounded ratio, not a rounded one.
- [ ] Reported ratio is truncated downward and no audit note contradicts its own verdict.
- [ ] `min_reserve_ratio_pct` reflects the mandate (100% = full reserves; higher for a buffer).

## Limitations recorded in the write-up

- [ ] Stated that one inclusion proof is evidence about one branch, not the whole tree.
- [ ] Stated that the on-chain reserve figure was an input, not something this check derived.
- [ ] Stated that the reserves are not shown to be unencumbered, unborrowed, or not shuttled between venues.
- [ ] Stated that a PoR engagement is not an audit (PCAOB advisory, 2023-03-08).
- [ ] Snapshot age is stated, and the result is not presented as a current-day guarantee.

## Cadence

- [ ] Verification is scheduled per publication, with root, root sum, ratio and timestamp persisted.
- [ ] Series is reviewed for falling declared liabilities, a static root, or reserves that appear only around publication dates.
