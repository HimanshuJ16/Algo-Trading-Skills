# Standards for Exchange Proof of Reserves Verification

## Status of Proof of Reserves

PoR is a **voluntary disclosure practice**, not a mandated one. No regulator reviewed
for this skill requires a centralised exchange to publish a cryptographic proof of
reserves, and there is no standard-setter's specification that exchange PoR schemes
conform to — each venue defines its own tree shape, preimage encoding and scope. The
100% reserve ratio below is the *definition* of full reserves, not a regulator-set
threshold. Treat any claim that a PoR publication is "regulated" or "audited" as
requiring separate evidence.

## Engineering requirements enforced by this skill

| Requirement | Basis |
|---|---|
| The audit path MUST rehash to the exchange's declared root. | Merkle inclusion is the only thing an individual proof establishes. |
| No balance on the verified branch may be negative. | Vitalik Buterin, *Having a safe CEX* (2022-11-19): an exchange concealing a shortfall adds "a -500 ETH balance under a fake account somewhere in the tree"; the branch balance audit is what makes that proof fail. |
| The root sum MUST equal the declared liability total. | A Merkle **sum** tree commits a total at its root; comparing it to the published figure is what a plain Merkle tree cannot support. Skipping it allows understated liabilities with every inclusion proof still passing. |
| The root sum check is only meaningful once the root hash matches. | Before authentication the recomputed sum is a sum over unverified nodes. |
| Leaf and interior hashes MUST use distinct domain-separation prefixes. | RFC 6962 §2.1: `MTH({d(0)}) = SHA-256(0x00 ‖ d(0))`, `MTH(D[n]) = SHA-256(0x01 ‖ …)`. "This domain separation is required to give second preimage resistance." |
| Balances MUST be canonicalised as exact fixed-point decimals before hashing or summing. | Binary floats cannot represent large stablecoin totals exactly and their sums are order-dependent, so a float verifier cannot reproduce a published root. Buterin's reference construction hashes integer balances (`to_bytes(32, 'big')`). |
| The solvency verdict MUST be taken on the unrounded ratio. | `round(99.999, 2) == 100.0`; rounding first certifies a deficit as full reserves. |
| Non-finite balances MUST raise, not be compared. | Every comparison against NaN is False, so NaN defeats a `< 0` guard and propagates into the ratio. |

## What PoR does not establish

**PCAOB, Office of the Investor Advocate — Investor Advisory, 2023-03-08,
"Exercise Caution with Third-Party Verification / Proof of Reserve Reports":**

- "PoR engagements are not audits and, consequently, the related reports do not
  provide any meaningful assurance to investors or the public."
- Reports "purport to provide an asset verification for an asset type at a
  particular moment in time, subject to significant limitations."
- "The procedures undertaken likely do not address the crypto entity's liabilities,
  the rights and obligations of the digital asset holders."
- They do not address "whether the assets have been borrowed by the crypto entity to
  make it appear they have sufficient collateral or 'reserves' in excess of customer
  demands."
- They "provide no assurance regarding the effectiveness of internal controls or of
  governance of the crypto entity."
- The engagements "are not subject to PCAOB auditing standards and the engagements
  are not subject to PCAOB inspection."

**Buterin (2022-11-19)** adds the collateral-shuttling limitation: moving collateral
between exchanges to cover successive snapshots "is something that exchanges could
easily do, and would allow them to pretend to be solvent when they actually are not."

## Scheme-specific notes

| Scheme | What it does | Bearing on this engine |
|---|---|---|
| **Binance zkPoR** | Replaced the original plain-Merkle scheme with a zk-SNARK circuit constraining, per user, that every asset balance is in the global state list, that the user's **total net** balance is not negative, and that the root transition is valid. | Binance states a Merkle inclusion proof alone "cannot independently verify that all balances sum correctly or that no negative net balances exist within the tree structure" — the boundary of what this engine can conclude. The constraint is on *net* balance across assets, so a per-asset tree forbidding negative leaves models a spot-only book. |
| **Binance, original (Nov 2022)** | Plain Merkle tree; leaves were hashes of holdings, so "the Merkle root couldn't reflect the sum of its leaf nodes' balance information." | The case for `enforce_root_sum_match=False`. The report then records `ROOT_SUM_UNENFORCED`: liabilities were taken on trust. |
| **Kraken PoR** | Merkle tree of hashed client balances with per-client proofs and an open-source verification tool; an independent accountancy firm confirms wallet control and that on-chain holdings exceed total client balances. Scope covers spot, margin, futures and staked balances. | Scope matters more than the tree: a proof covering only spot balances understates liabilities regardless of how well the hashing verifies. |

## Sources

- Vitalik Buterin, "Having a safe CEX: proof of solvency and beyond", 2022-11-19 —
  <https://vitalik.eth.limo/general/2022/11/19/proof_of_solvency.html>
- RFC 6962 §2.1, "Certificate Transparency — Merkle Hash Trees" —
  <https://www.rfc-editor.org/rfc/rfc6962#section-2.1>
- PCAOB Office of the Investor Advocate, Investor Advisory, 2023-03-08 —
  <https://pcaobus.org/resources/information-for-investors/investor-advisories/investor-advisory-exercise-caution-with-third-party-verification-proof-of-reserve-reports>
- Binance, "How zk-SNARKs Improve Binance's Proof of Reserves System" —
  <https://www.binance.com/en/blog/tech/how-zksnarks-improve-binances-proof-of-reserves-system-6654580406550811626>
- Kraken, "Proof of Reserves" — <https://www.kraken.com/proof-of-reserves>
