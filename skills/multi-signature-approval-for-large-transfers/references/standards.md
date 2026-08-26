# Standards for Multi-Signature Approval for Large Transfers

## Status of these requirements

The tier thresholds ($10,000 / $100,000), the 2-of-3 and 3-of-5 splits and the
one-hour timelock in this skill are **firm policy defaults, not regulation**. No
securities, banking or virtual-asset regulator surveyed below prescribes a
specific $M$-of-$N$ threshold, a dollar tier, or a timelock duration for crypto
transfers. NYDFS Part 200 and its custody guidance address segregation of
customer assets, recordkeeping and insolvency protection rather than approval
quorums. Treat every number in `MultiSigConfig` as a value your firm must set
from its own risk appetite and defend to its own auditor.

What *is* externally grounded is the shape of the control — separation of duties,
multiple key holders on distinct devices, and signing over a payload digest —
which is what the sources below support.

## Engineering standards enforced by the engine

| Standard | Rule | Enforced by |
|---|---|---|
| Enumerated quorum | Only signers on the registered roster count toward $M$. An unregistered id is `SIGNER_NOT_ON_ROSTER`. | `register_signer` / `_screen_approvals` |
| Honest $N$ | If the eligible roster is smaller than the policy's $N$, the report says so in `warnings` rather than silently claiming a threshold that does not exist. | `evaluate_transfer_approval` |
| Role separation | A quorum must span `*_distinct_roles_required` distinct roster roles; a declared role that disagrees with the roster is rejected as a tamper signal. | `_screen_approvals` / `_decide` |
| Payload binding | An approval must carry the digest of the payload the signer reviewed. Deny by default (`require_payload_binding=True`). | `compute_transfer_digest` / `_screen_approvals` |
| Unambiguous digest | Every digest field is length-prefixed under a domain separator, so no field-boundary shuffle collides. | `_length_prefixed` |
| Trusted clock | The timelock is measured from the engine's first observation of the digest, never from `request.creation_timestamp`. | `register_request` / `evaluate_transfer_approval` |
| Stable anchor | Re-registering the same payload keeps the original anchor; a past anchor can only be set through the named `restore_timelock_anchor`. | `register_request` |
| Re-anchor on change | Any change to destination, chain, asset, quantity, notional or nonce yields a new digest, a fresh timelock, and no surviving approvals. | `compute_transfer_digest` |
| Abortable window | `revoke_request` is keyed on `request_id` and survives a nonce bump; `suspend_signer` withdraws a compromised signer's approvals from the count. | `revoke_request` / `suspend_signer` |
| Execute once | An executed digest returns `ALREADY_EXECUTED`; marking one twice raises. | `mark_executed` |
| Fail loudly | Non-finite/non-positive notionals, blank identifiers, $M > N$, inverted thresholds and negative timelocks raise rather than being scored. | `MultiSigApprovalError` |
| Auditable rejection | Every discarded approval is recorded with a machine-readable reason, not silently dropped. | `MultiSigApprovalReport.rejected_approvals` |

## Control framework touchpoints (verified 2026-08)

| Source | Identifier | What it actually says | How this skill relates |
|---|---|---|---|
| NIST SP 800-53 Rev. 5 | `AC-5` Separation of Duties | "Define system access authorizations to support separation of duties." The discussion frames SoD as reducing "the risk of malevolent activity without collusion" by dividing functions among different individuals or roles. | The distinct-role requirement and the initiator self-approval block are this control applied to a transfer decision. AC-5 mandates *that* duties be separated; it prescribes no quorum size. |
| CCSS v9 (C4) | `1.02.1` Signing Configuration | Applying a multi-signer mechanism to a wallet holding bulk customer funds is stated as **best practice** at Levels II and III — not a hard pass/fail requirement, and not required at Level I. | Do not tell an auditor that CCSS *mandates* multisig. It commends it. |
| CCSS v9 (C4) | `1.05.9` Multi-Signer Mechanism Usage | Key material for a wallet implementing a multi-signer mechanism is stored and used on **different logical or physical devices**, at all three levels. | The roster models distinct signers; device separation is an operational control outside this engine and belongs in the checklist. |
| CCSS v9 (C4) | `1.02.2` Key Material Redundancy | A wallet with a multi-signer mechanism has at least one redundant key for recovery (Levels II/III). | Why $N > M$ matters: the engine warns when the eligible roster has shrunk to $N$ or below, because losing one more signer makes the quorum unreachable. |
| CCSS v9 (C4) | `1.04.1` – `1.04.3` Key Holder Grant/Revoke | Grant/revoke checklists at Level I; over an Approved Communication Channel at Level II; with an audit trail naming who performed the operation at Level III. | `register_signer` / `suspend_signer` / `reinstate_signer` are the grant-revoke surface; the audit trail obligation is on the caller's logging. |
| Safe Smart Account (v1.4.1) | `SAFE_TX_TYPEHASH` | Owners sign an EIP-712 digest over `to`, `value`, `data`, `operation`, `safeTxGas`, `baseGas`, `gasPrice`, `gasToken`, `refundReceiver`, `nonce`. "Each transaction should have a different nonce to prevent replay attacks." | `compute_transfer_digest` is the same idea in an off-chain gate: approvals bind to a payload and a nonce, not to a request id. |

## Incident evidence

| Incident | Confirmed facts | Consequence for this skill |
|---|---|---|
| Bybit, 21 Feb 2025, 14:13 UTC | ~$1.46bn (401,347 ETH, 90,375 stETH, 15,000 cmETH, 8,000 mETH) left one Ethereum multisig cold wallet. Bybit: "Hackers exploited the UI of the Safe multisig cold wallet through a sophisticated phishing attack, masking the specific transaction, which resulted in the change in smart contract logic". Safe confirmed no compromise of its codebase or dependencies; the signing keys themselves were **not** stolen. | A valid $M$-of-$N$ quorum signed a payload that was not the one displayed. Threshold size is no defence against a single falsified presentation shared by all signers — hence the out-of-band destination verification in the checklist, and why `approved_digest` records what was approved rather than that approval occurred. |

## What this engine deliberately does not claim

- It does not verify signature authenticity. `approved_digest` records *which*
  payload a signer approved; proving *that they approved it* is the identity
  layer's job.
- It does not enforce anything on-chain. The vault's own threshold is the
  authoritative control.
- It holds no cross-process state, so it cannot prevent a double release by two
  independent workers.

## Sources

- NIST SP 800-53 Rev. 5, AC-5 Separation of Duties — https://csf.tools/reference/nist-sp-800-53/r5/ac/ac-5/
- NIST SP 800-53 Rev. 5 (publication) — https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final
- CryptoCurrency Certification Consortium, CCSS v9 requirements matrix — https://cryptoconsortium.org/ccss-table-v9/
- CryptoCurrency Certification Consortium, CCSS overview and levels — https://cryptoconsortium.org/cryptocurrency-security-standard-documentation/overview/
- Safe Smart Account contracts v1.4.1 (`SAFE_TX_TYPEHASH`, `encodeTransactionData`) — https://github.com/safe-global/safe-smart-account/blob/v1.4.1/contracts/Safe.sol
- Safe Smart Account documentation overview — https://github.com/safe-global/safe-smart-account/blob/main/docs/overview.md
- Bybit, "Bybit Security Incident: Timeline of Events and FAQs" — https://www.bybit.com/en/learn/this-week-in-bybit/bybit-security-incident-timeline
- Halborn, "Explained: The Bybit Hack (February 2025)" — https://www.halborn.com/blog/post/explained-the-bybit-hack-february-2025
- NYDFS, Virtual Currency Business Licensing (23 NYCRR Part 200) — https://www.dfs.ny.gov/virtual_currency_businesses
- NYDFS Industry Letter, "Updated Guidance on Custodial Structures for Customer Protection in the Event of Insolvency" (30 Sep 2025) — https://www.dfs.ny.gov/industry-guidance/industry-letters/il20250930-updated-guidance-custodial-structures
