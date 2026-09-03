---
name: smart-contract-approval-scope-minimization
description: >-
  Use when a bot grants ERC-20 spending rights to a DeFi protocol, refusing unlimited
  allowances and sizing each approval to the exact transaction notional, with expiring
  Permit2 allowances preferred where available.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: crypto-custody-security
  tags: approval-scope-minimization, erc-20-allowance, eip-2612-permit, unlimited-approval-risk, revoke-to-zero, defi-security
  brokers_frameworks: "EIP-20; EIP-2612 Permit Standard; EIP-712 Typed Data; Uniswap Permit2; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when an automated trading bot grants ERC-20 spending rights to a DeFi protocol — DEX routers and aggregators, lending pools, yield vaults, Permit2. An unlimited allowance (`type(uint256).max`, or `type(uint160).max` on Permit2) is permanent standing authority: if the spender contract is exploited years later, the attacker drains the wallet's entire balance of that token without any further action by the owner. This engine decides the allowance to grant, whether an `approve(spender, 0)` reset must precede it, whether an EIP-2612 permit can replace the approval entirely, and which existing allowances must be revoked.

## When NOT to Use

- **Not a transaction builder or signer.** It returns plans, not calldata and not signatures. It never touches an RPC endpoint or a private key.
- **Not an allowance scanner.** It has no chain access; the caller supplies the allowance inventory and the current on-chain allowance.
- **Not a substitute for auditing the spender.** A minimized allowance limits blast radius; it does not make an unaudited contract safe — see `smart-contract-audit-requirements-before-defi-integration`.
- **Not applicable to ERC-721/ERC-1155 `setApprovalForAll`**, which is all-or-nothing and has no amount to minimize.

## Prerequisites

- Approval request: `token_address`, `spender_address`, `required_amount` (token **base units**, not human units), `supports_eip2612_permit`.
- The current on-chain allowance, read immediately before planning, passed via `plan_approval(current_allowance=...)` or `record_allowance(TokenAllowance(...))`. Absent both, the engine assumes 0 and will not plan a zero-reset.
- For the audit path: a `TokenAllowance` inventory, optionally with `last_used_unix` for staleness.
- For an EIP-2612 plan, the caller must separately fetch `nonces(owner)` from the token and build the EIP-712 domain (name, version, chainId, verifying contract). The engine supplies only the value and deadline.

## Workflow

1. **Unlimited approval block** — `plan_approval` raises `UnlimitedApprovalBlocked` for `type(uint256).max`, for Permit2's `type(uint160).max`, and for anything at or above `unlimited_allowance_threshold`. It fails closed rather than returning a status field, because a caller that ignored such a field would grant the infinite allowance this skill exists to prevent. Use `classify_requested_amount()` when a non-throwing check is wanted first.
2. **Exact sizing** — the recommended allowance is the requested notional in base units. Decimals are the caller's responsibility: 100 USDC is `100_000000` (6 decimals), 100 DAI is `100 * 10**18`.
3. **EIP-2612 permit path** — if the token implements ERC-2612, plan a signed permit with a deadline of `int(now) + ceil(validity)` seconds. `deadline` is emitted as an `int` because ERC-2612 compares it to `block.timestamp`; a float will not ABI-encode as `uint256`. Validity above `max_permit_validity_seconds` (default 600s) is rejected. A permit overwrites the allowance in one call, so no zero-reset applies.
4. **Approve-to-zero reset** — on the standard `approve()` path, a reset is planned only when the current allowance is non-zero **and** the new amount is non-zero **and** they differ. Revoking to 0 needs no preparatory reset, and an allowance already equal to the target needs no transaction at all (`approval_transaction_needed` is `False`).
5. **Revocation audit** — `audit_allowances()` plans `approve(spender, 0)` for every unlimited allowance, and, when `stale_after_seconds` is supplied, for every non-zero allowance idle longer than that window. Allowances already at 0 are skipped; an unknown `last_used_unix` is never treated as stale.

> Full procedure: see `references/workflows.md`.
> Standards and house policy, separated: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Granting `uint256.max` for a single swap.** The convenience saves one transaction and leaves a permanent claim on the whole balance. Every historical approval-drain exploit monetized allowances granted for trades that had long since settled.
- **Checking only `uint256.max` when auditing.** Permit2 amounts are `uint160`, so its unlimited sentinel is `type(uint160).max` ≈ `1.46e48` — twelve orders of magnitude below a naive `2**200` "obviously huge" threshold. A uint256-only scan reports a Permit2-unlimited wallet as clean.
- **Re-approving over a non-zero allowance.** EIP-20 warns the spender can front-run the change and spend the old and new amounts. Some tokens enforce the reset in the contract: USDT (`0xdAC17F958D2ee523a2206206994597C13D831ec7`) reverts on `approve` when the existing allowance and the new value are both non-zero, so the trade fails outright rather than merely being unsafe.
- **Assuming `approve()` returns a bool.** USDT and other pre-EIP-20-finalization tokens return nothing; an interface declaring `returns (bool)` reverts on return-data decoding. Use a safe-transfer wrapper for the submission step — outside this engine's scope.
- **Planning against a stale allowance.** The zero-reset decision is only as good as the `current_allowance` supplied. Re-read it immediately before submitting; a fill or a partial spend between read and submit changes the answer.
- **Emitting a float permit deadline.** `time.time()` returns a float; ERC-2612's `deadline` is a `uint256` second count. Convert to an integer before encoding, rounding the window up so a sub-second validity does not truncate to an already-expired deadline.
- **Treating the 600-second permit cap as a standard.** ERC-2612 prescribes no maximum deadline. The cap here is house policy — tighten or loosen it deliberately.
- **Leaving stale approvals on retired protocols.** An allowance whose strategy was decommissioned months ago carries the same risk as an active one and earns nothing.

## Verification

- Instantiate `SmartContractApprovalScopeMinimizationEngine(clock=lambda: 1_700_000_000.0)`.
- Plan 100 USDC (`required_amount=100_000000`, no permit): verify `recommended_approval_amount == 100000000`, `approval_type == EXACT_AMOUNT`, `requires_reset_to_zero_first is False`.
- Record a 50 USDT allowance for the same spender, then plan 100 USDT: verify `requires_reset_to_zero_first is True`. Plan 100 with `current_allowance=100_000000`: verify `approval_transaction_needed is False`. Plan 0 over a non-zero allowance: verify no reset is required.
- Plan 500 DAI with `supports_eip2612_permit=True, permit_validity_seconds=600`: verify `approval_type == EIP_2612_PERMIT` and `permit_deadline_unix == 1700000600` as an `int`. Verify `permit_validity_seconds=3600` raises.
- Negative checks: `MAX_UINT256` and `MAX_UINT160` each raise `UnlimitedApprovalBlocked` (on both the permit and non-permit paths); `(1 << 200) - 1` is allowed; a negative amount, a float amount, a malformed address, and the zero address each raise.
- Audit an inventory containing `MAX_UINT160`: verify one revocation plan to 0. Audit a 90-day-idle allowance with `stale_after_seconds=30*86400`: verify one revocation plan; with no window, verify none.
- Run `python -m unittest discover -s skills/smart-contract-approval-scope-minimization/scripts` (36 tests).

## Related Skills

- `smart-contract-audit-requirements-before-defi-integration`
- `segregation-of-duties-for-custody-operations`
- `hot-cold-wallet-split-for-trading-bots`
- `decentralized-exchange-dex-integration-uniswap-style`
