# Workflows — smart-contract-approval-scope-minimization

## A. Planning a single approval

1. **Read the current allowance fresh.** Call `allowance(owner, spender)` on the token
   immediately before planning and pass the result as
   `plan_approval(current_allowance=...)`, or store it with `record_allowance()`. With
   neither, the engine assumes 0 and will not plan a zero-reset.
2. **Convert to base units.** `required_amount` is an integer in the token's own
   decimals — 100 USDC is `100_000000`, 100 DAI is `100 * 10**18`. Never pass a float.
3. **Let the unlimited block run.** `plan_approval` raises `UnlimitedApprovalBlocked`
   for `uint256.max`, `uint160.max`, and anything at or above the threshold. Do not
   catch it and fall back to approving anyway; fix the caller that asked for it. Use
   `classify_requested_amount()` for a non-throwing pre-check.
4. **Choose the path.**
   - Token implements ERC-2612 → permit plan. Fetch `nonces(owner)`, build the EIP-712
     domain (name, version, **chainId**, verifying contract), sign, and submit
     `permit(...)` with the plan's integer `permit_deadline_unix`. The chain ID is what
     prevents cross-chain replay and the engine does not model it — get it right.
   - Otherwise → exact-amount `approve()` plan.
5. **Honour `requires_reset_to_zero_first`.** Submit `approve(spender, 0)`, wait for it
   to be **mined**, then submit `approve(spender, amount)`. Sending both in one block
   without ordering guarantees reintroduces the race the reset exists to close.
6. **Skip no-op transactions.** `approval_transaction_needed is False` means the
   allowance already equals the target; sending anything burns gas and reopens a
   front-running window for nothing.
7. **Re-check before submission.** If the allowance changed between the read and the
   broadcast, re-plan rather than submitting the stale plan.

## B. Periodic revocation audit

1. Build the `TokenAllowance` inventory from chain data (approval-event history or an
   indexer), including allowances granted to protocols the desk no longer uses.
2. Set `last_used_unix` from transfer history where it is known. Leave it `None` where
   it is not — the engine will not guess, and unknown age is never called stale.
3. Call `audit_allowances(inventory, stale_after_seconds=...)`. Unlimited allowances are
   always planned for revocation; stale ones only when a window is given.
4. Submit each plan as `approve(spender, 0)`. On Permit2, revoke through Permit2's own
   `approve(token, spender, 0, expiration)` — zeroing the ERC-20 allowance *to* Permit2
   blocks pulls today but does not clear the permissions Permit2 has already recorded
   for downstream spenders, which become live again the moment Permit2 is re-approved.
5. Re-run on a schedule. An allowance survives strategy decommissioning, key rotation,
   and protocol migration; nothing revokes it implicitly.

## C. Escalation

- An `UnlimitedApprovalBlocked` raised in production is a caller defect, not a transient
  error — alert, do not retry.
- An audit that returns revocations for a protocol nobody recognizes is a possible
  compromise indicator; treat it under
  `post-incident-forensics-for-suspected-key-compromise` before assuming it is stale
  housekeeping.
