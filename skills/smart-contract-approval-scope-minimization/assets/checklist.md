# Pre-Flight Checklist — Approval Scope Minimization

## Before granting

- [ ] Is `required_amount` an integer in the token's **base units** (decimals applied)?
- [ ] Does the caller refuse `uint256.max` **and** Permit2's `uint160.max`, without catching `UnlimitedApprovalBlocked` and approving anyway?
- [ ] Was the current allowance read from the chain immediately before planning, and passed in?
- [ ] Is the spender address the intended contract — verified against the protocol's published deployment, not a value copied from a transaction that failed?
- [ ] For an ERC-2612 permit: is `nonces(owner)` fetched fresh and is the EIP-712 domain built with the correct **chainId** and verifying contract?
- [ ] Is `permit_deadline_unix` encoded as an integer `uint256`, and is the validity window as short as the workflow tolerates?

## Before submitting

- [ ] If `requires_reset_to_zero_first` is set, is `approve(spender, 0)` **mined** before the new approval is sent?
- [ ] If `approval_transaction_needed` is `False`, is the transaction skipped rather than re-sent?
- [ ] Does the submission layer tolerate tokens whose `approve` returns no value (USDT and similar)?
- [ ] Has the allowance been re-checked if the plan has been sitting unsent?

## Ongoing

- [ ] Does the revocation audit run on a schedule, not only after an incident?
- [ ] Is `last_used_unix` populated where transfer history is available, so staleness is actually evaluated?
- [ ] Are allowances to decommissioned strategies and retired protocols revoked to 0?
- [ ] Are Permit2 permissions revoked through Permit2 itself, not only by zeroing the ERC-20 allowance to it?
- [ ] Are `unlimited_allowance_threshold` and `max_permit_validity_seconds` deliberately calibrated rather than left at the library defaults?
