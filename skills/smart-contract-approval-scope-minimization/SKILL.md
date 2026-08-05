---
name: smart-contract-approval-scope-minimization
description: >-
  Production-grade DeFi smart contract approval scope minimization engine enforcing exact allowance sizing, EIP-2612 off-chain permit optimization, approve-to-zero race condition protection, and unlimited allowance revocation audits.
domain: Crypto Custody & DeFi Security
subdomain: Smart Contract Allowance Security
tags: ["approval-scope-minimization", "erc-20-allowance", "eip-2612-permit", "unlimited-approval-risk", "revoke-to-zero", "defi-security"]
brokers_frameworks: ["EIP-2612 Permit Standard", "EIP-712 Typed Data", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when interacting with DeFi protocols (DEX aggregators, lending pools, yield vaults) via automated trading bots. Granting unlimited ERC-20 token approvals (`uint256.max`) creates permanent security vulnerabilities: if a protocol smart contract is exploited in the future, attackers can drain all tokens from the approving wallet even years later. This engine restricts approvals to exact required transaction amounts, leverages EIP-2612 off-chain signed permits with deadlines, and enforces `approve(spender, 0)` resets to prevent race conditions.

## Prerequisites

- Token allowance specification (`token_address`, `spender_address`, `required_amount`, `supports_eip2612_permit`).
- Active allowance inventory (`TokenAllowance`: `token_address`, `spender_address`, `current_allowance`, `is_unlimited`).

## Workflow

1. **Unlimited Approval Block**:
   - Reject any request for `uint256.max` ($2^{256}-1$) unlimited allowance.
2. **EIP-2612 Permit Prioritization**:
   - If token supports EIP-2612, issue off-chain signed permit with short validity deadline (e.g. 300s).
3. **Approve-to-Zero Race Condition Protection**:
   - If token uses standard `approve()` and existing allowance $> 0$ and $\neq \text{required\_amount}$, issue `approve(spender, 0)` transaction prior to new approval.
4. **Stale Allowance Revocation Audit**:
   - Audit active allowances; generate zero-reset plans for any high-risk unlimited approvals.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Granting Unlimited `uint256.max` Allowances**: Approving infinite token spending for minor swap transactions, exposing wallet balances to protocol exploits.
- **Ignoring ERC-20 Race Conditions**: Updating an existing non-zero allowance without resetting to zero first, allowing spenders to front-run the approval change.
- **Unused Stale Allowances**: Leaving active approvals on historical protocols long after trading interactions have ceased.

## Verification

- Instantiate `SmartContractApprovalScopeMinimizationEngine`. Plan approval for $100 USDC $\implies$ verify `recommended_approval_amount = 100000000` and `EXACT_AMOUNT` type. Plan DAI approval with EIP-2612 support $\implies$ verify `EIP_2612_PERMIT` type with Unix deadline. Audit `uint256.max` allowance $\implies$ verify revocation plan generated to reset allowance to 0.
- Run `python scripts/test_smart_contract_approval_scope_minimization.py`.

## Related Skills

- `smart-contract-audit-requirements-before-defi-integration`
- `segregation-of-duties-for-custody-operations`
---
