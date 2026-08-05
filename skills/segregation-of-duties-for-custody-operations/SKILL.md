---
name: segregation-of-duties-for-custody-operations
description: >-
  Production-grade Segregation of Duties (SoD) & Maker-Checker dual control engine for institutional crypto custody operations, enforcing M-of-N threshold approvals, role-based access control (RBAC), and self-approval prevention for SOC 2 compliance.
domain: Crypto Custody & Security
subdomain: Segregation of Duties & Governance Controls
tags: ["segregation-of-duties", "maker-checker", "dual-control", "m-of-n-approval", "crypto-custody", "soc-2-compliance"]
brokers_frameworks: ["Institutional Custody Governance", "SOC 2 Type II Controls", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when designing or auditing operational risk governance for institutional crypto custody, digital asset treasury management, or SOC 2 Type II compliance. Segregation of Duties (SoD) ensures no single individual possesses end-to-end unilateral authority to initiate, approve, and execute a custody transfer or key policy change. This engine enforces the Maker-Checker paradigm (Initiators cannot approve their own proposals), RBAC role separation, and $M$-of-$N$ threshold approvals for large transfers.

## Prerequisites

- User identity definitions (`UserIdentity`: `user_id`, `username`, `department`, `roles`: `INITIATOR`, `APPROVER`, `SECURITY_ADMIN`, `AUDITOR`).
- Large transfer threshold USD (default $50,000.00 requiring 2 approvals).

## Workflow

1. **User Identity & Role Conflict Screening**:
   - Register user; reject invalid role combinations (e.g. `SECURITY_ADMIN` + `INITIATOR` $\implies$ throws `SoDConflictError`).
2. **Transfer Proposal Creation (Maker Step)**:
   - User with `INITIATOR` role proposes transfer. System calculates required approvals based on dollar notional.
3. **Dual Control Approval Inspection (Checker Step)**:
   - User with `APPROVER` role inspects proposal.
   - Enforce Maker-Checker rule: if `approver_id == initiator_id`, throw `SoDConflictError("SELF_APPROVAL_ATTEMPT")`.
4. **Cryptographic Audit Trail & Status Update**:
   - Generate SHA-256 approval signature hash. Transition proposal status to `APPROVED` once $M$-of-$N$ threshold is met.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Permitting Self-Approval for Small Amounts**: Allowing initiators to approve lower-value transfers without checker oversight.
- **Combined Admin & Maker Roles**: Assigning security administrator rights to operational traders who initiate transfers.
- **Unsigned Manual Approval Logs**: Relying on unverified chat or email messages rather than cryptographically signed audit logs for SOC 2 evidence.

## Verification

- Instantiate `SegregationOfDutiesForCustodyOperationsEngine`. Propose $100,000 transfer $\implies$ verify 2 approvals required. Have Maker attempt self-approval $\implies$ verify `SoDConflictError` raised. Have Checker 1 and Checker 2 approve $\implies$ verify status transitions to `APPROVED`. Attempt to register user combining Admin and Initiator roles $\implies$ verify registration rejected.
- Run `python scripts/test_segregation_of_duties_for_custody_operations.py`.

## Related Skills

- `multi-signature-approval-for-large-transfers`
- `withdrawal-velocity-limits-and-anomaly-detection`
---
