---
name: multi-signature-approval-for-large-transfers
description: >-
  Multi-signature approval workflow engine enforcing M-of-N threshold tiers, distinct role-based signer verification, and timelock delays for large crypto transfers.
domain: Crypto Custody Security
subdomain: Multi-Signature Approval Workflows & Governance Controls
tags: ["multisig", "transfer-approval", "m-of-n", "timelock", "governance", "crypto-custody", "role-based-access"]
brokers_frameworks: ["Multisig Policy Engine", "Role-Based Access Control (RBAC)", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when implementing corporate governance and security controls for cryptocurrency asset transfers. Operational trading bots frequently require automated liquidity rebalancing, but allowing single-signature automated withdrawals creates catastrophic loss risk if a bot or private key is compromised. This engine enforces **Tiered $M$-of-$N$ Threshold Approval Matrices**, distinct signer verification (preventing self-approval), and mandatory timelock delays for high-value transfers ($>\$100,000$ USD).

## Prerequisites

- Tier threshold configuration (`auto_approve_threshold_usd`: e.g. $\$10,000$, `high_value_threshold_usd`: e.g. $\$100,000$, `med_m_of_n`: (2, 3), `high_m_of_n`: (3, 5), `timelock_seconds`).
- Transfer request payload (`request_id`, `amount_usd`, `source_wallet`, `destination_address`, `initiated_by`).
- Signer approval submissions (`signer_id`, `role`, `timestamp`).

## Workflow

1. **Transfer Tier Classification**:
   - Classify request into risk tier based on USD value:
     - **Low Tier** ($< T_{\text{auto}}$): Auto-Approved / $1$-of-$1$.
     - **Medium Tier** ($T_{\text{auto}} \le \text{Val} \le T_{\text{high}}$): $2$-of-$3$ distinct signer approval.
     - **High Tier** ($> T_{\text{high}}$): $3$-of-$5$ distinct signer approval + Timelock delay.
2. **Distinct Signer Verification**:
   - Collect signer approvals. Reject duplicate signatures from the same `signer_id` or self-approval by `initiated_by`.
3. **Timelock Delay Audit**:
   - For High-Tier transfers, verify elapsed time since initiation meets timelock requirement:
     $$t_{\text{current}} - t_{\text{init}} \ge t_{\text{timelock}}$$
4. **Audit Report Generation**: Output structured `MultiSigApprovalReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Self-Approval Vulnerability**: Allowing the request initiator to supply all required signature approvals.
- **Duplicate Signer Reuse**: Counting multiple approvals from a single compromised signer account toward the threshold requirement.
- **Bypassing Timelock Delays**: Executing high-tier transfers immediately without giving security teams a window to abort unauthorized requests.

## Verification

- Instantiate `MultiSigApprovalEngine`. Audit $\$500,000$ USD high-tier transfer request $\implies$ submit 3 distinct signatures (CFO, Risk Officer, Security Officer) $\implies$ verify $3$-of-$5$ threshold, timelock check, and status `TRANSFER_APPROVED`.
- Run `python scripts/test_multi_signature_approval_for_large_transfers.py`.

## Related Skills

- `multi-party-computation-mpc-custody-solutions`
- `withdrawal-velocity-limits-and-anomaly-detection`
---
