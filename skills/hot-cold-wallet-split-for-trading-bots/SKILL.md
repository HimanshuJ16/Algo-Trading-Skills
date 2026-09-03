---
name: hot-cold-wallet-split-for-trading-bots
description: >-
  Use when deciding how much crypto capital should be reachable by the trading key right
  now, proposing idempotent sweep and refill transfers that net in-flight movements and
  respect a hot-wallet ceiling.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: crypto-custody-security
  tags: crypto-custody, hot-wallet, cold-storage, treasury-management, rebalance-sweep, api-key-security, circuit-breaker
  brokers_frameworks: "Fireblocks; BitGo; Coinbase Custody; Binance API; SFC VATP Guidelines; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a crypto trading bot holds working capital and you need a defensible answer to "how much should be reachable by the trading key right now, and what moves when it isn't?" Keeping 100% of capital in exchange hot wallets or online keys exposes it to counterparty collapse (FTX), hot wallet compromise, and API key theft. The engine turns a balance snapshot plus the trading key's permission set into an auditable **sweep**, **refill**, or **hold** proposal, and escalates a mandated hot-wallet ceiling separately from an ordinary rebalance.

It is a **stateless, point-in-time policy evaluator**. It proposes; it never signs, broadcasts, or tracks a transaction.

## When NOT to Use

- **As the executor.** The engine returns a proposal. Signing, broadcasting, confirming, and retrying are the caller's job, and the caller owns transfer idempotency — feed settled results back through `pending_transfer_*` rather than assuming the engine remembers anything.
- **As a compliance determination for client assets.** The mandated caps below apply to *client* virtual assets held by a licensed platform. If you custody customer funds, the binding number is your regulator's, and its measurement basis (Korea marks monthly on a trailing-year average KRW value, not on a live snapshot) may not be the instantaneous ratio this engine computes.
- **As a per-venue exposure control.** The engine models one aggregate hot balance. Ten exchanges each at 15% is a 15% hot ratio and ten separate counterparty failure points. Cap per-venue exposure separately.
- **On unmarked or multi-currency balances.** Inputs are USD-denominated. The engine has no price source and cannot detect a stale or inconsistent mark, so a ratio computed from mixed-timestamp marks is garbage the engine will happily act on.
- **As a full API key audit.** `is_api_key_secure` means "no fund-moving permission is enabled among the three checked". It says nothing about key age, rotation, storage, or scope creep — see `api-key-least-privilege-audit-tool`.

## Prerequisites

- Balances marked to USD at a single consistent timestamp: `hot_wallet_usd`, `cold_vault_usd`, `warm_buffer_usd`. All must be finite and non-negative.
- Unsettled transfers already submitted: `pending_transfer_to_cold_usd`, `pending_transfer_to_hot_usd`. **Omitting these is the single most expensive mistake here** — a scheduled audit that cannot see its own in-flight sweep will re-propose it on every run.
- The trading key's actual permission set, read from the exchange rather than assumed. Binance's Get API Key Permission endpoint returns `enableWithdrawals`, `enableInternalTransfer`, `permitsUniversalTransfer`, and `ipRestrict`; all four matter.
- A threshold band. The defaults (`target_hot_ratio=0.15`, `max_hot_ratio_threshold=0.25`, `min_hot_ratio_threshold=0.05`) are **engineering defaults with no regulatory basis**. Set `regulatory_max_hot_ratio` if a mandated ceiling binds you.

## Workflow

1. **Validate before computing.** Every balance is checked for finiteness and non-negativity, and the total for strict positivity. This is not ceremony: `NaN` compares `False` against every threshold, so an unvalidated corrupt balance falls through to a confident `PORTFOLIO_BALANCED`. The engine raises `HotColdWalletError` (a `ValueError` subclass) instead.
2. **Net in-flight transfers before deciding.** Total = Hot + Cold + Warm. Effective Hot = Hot − pending-to-cold + pending-to-hot. Pending transfers are still inside the treasury, so they change the hot balance the treasury is *converging to* without changing the denominator. **Decisions use the effective ratio; the report carries both** — `current_hot_ratio` for the audit trail, `effective_hot_ratio` for the decision.
3. **Audit every fund-moving permission, not just withdrawals.** `enableWithdrawals=false` is not sufficient: `enableInternalTransfer` and `permitsUniversalTransfer` each move funds on their own. Any one of the three triggers `CRITICAL_SECURITY_ALERT`, halts rebalancing, and returns a zero transfer — you do not route treasury movements through a key you are about to revoke. An unassessed `api_key_ip_restricted` is reported as unassessed rather than counted as hardened.
4. **Compare on unrounded ratios.** Rounding the ratio before the threshold test lets a 25.004% breach round to exactly 25.00% and pass a strict `> 25%` control. Rounding is applied to the reported figures only.
5. **Size the transfer, then cap it at what the funding wallet actually holds.** A sweep can only move coins settled and uncommitted in the hot wallet; a refill can only draw cold that is not already committed to an in-flight refill. When the requirement exceeds availability the proposal is capped and `is_transfer_fully_fundable` goes `False` — never hand an automated executor an instruction that cannot settle.
6. **Escalate a mandated breach distinctly.** With `regulatory_max_hot_ratio` set, the ceiling binds ahead of the engine's own band: the sweep trigger becomes `min(max_hot_ratio_threshold, cap)` and the sweep target `min(target_hot_ratio, cap)`. Breaching the cap yields status `REGULATORY_HOT_CAP_BREACH` and a `CRITICAL` log line, not an ordinary `REBALANCE_REQUIRED`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Re-proposing a sweep that is already in flight.** Chain settlement is slow and this engine is stateless. Run the audit every minute against raw balances and an automated executor will fire the same $150k sweep sixty times before the first one confirms. Pass `pending_transfer_to_cold_usd` so the engine proposes the *increment*: with $40k already moving out of a $300k hot wallet, it asks for $110k, and the two transfers sum to exactly the $150k a single sweep would have moved.
- **Double-counting a broadcast transfer.** A pending amount must still be counted in its *source* wallet — an unsettled sweep is part of `hot_wallet_usd`, an unsettled refill part of `cold_vault_usd`. Most balance feeds debit a transfer the moment it is broadcast; passing it as pending *as well* subtracts it twice, collapsing the effective ratio and provoking a spurious refill in the opposite direction. The engine rejects the incoherent case (`pending > source balance`) rather than acting on it, but a partial double-count within the balance is still yours to avoid.
- **Treating `enableWithdrawals=false` as "the key cannot move funds".** It cannot reach an external address, but `enableInternalTransfer` and `permitsUniversalTransfer` still shift balances between the operator's own account types and products — enough for an attacker to strand funds where the bot cannot trade them, and enough to defeat a withdrawal-whitelist control.
- **Rounding the ratio before testing it against the cap.** A treasury at 25.004% is over a 25% cap. Round-then-compare silently reports it as balanced, and the error is invisible precisely at the boundary the control exists to defend.
- **Proposing a refill the cold vault cannot fund.** Ratio arithmetic says "move $120,000 from cold to hot" regardless of whether cold holds $120,000. Check fundability, or the executor fails mid-run in a state neither wallet expects.
- **Assuming 15%/25%/5% is a standard.** It is not. No US regulator prescribes a numeric split — NYDFS 23 NYCRR Part 200 imposes custody and segregation duties without a percentage. The numbers that *are* mandatory are far stricter and apply to client assets: 2% (Hong Kong), 5% (Japan), 20% (South Korea). Running the defaults while holding client assets in those jurisdictions is a licence problem, not a tuning choice.
- **Configuring a band that cannot converge.** A 30% target under a 25% cap proposes a "fix" that leaves the treasury at 30% — still breaching the cap that triggered it. Likewise a 5% operating floor under a 2% mandated ceiling guarantees a permanent breach every time the treasury refills to its own floor. Both configurations are rejected at construction.
- **Reading one aggregate ratio as counterparty safety.** The engine's denominator is the whole treasury. It cannot tell 15% on one exchange from 15% spread across ten.

## Verification

- Hot \$300,000 / Cold \$700,000 (total \$1M, 30% > 25%) ⟹ `SWEEP_TO_COLD` of \$150,000 down to the 15% target. Hot \$30,000 / Cold \$970,000 (3% < 5%) ⟹ `REFILL_HOT_FROM_COLD` of \$120,000.
- Re-run the first case with `pending_transfer_to_cold_usd=150_000.0` and confirm `HOLD_BALANCES` with a \$0 proposal, `current_hot_ratio == 0.30` and `effective_hot_ratio == 0.15`. With \$40,000 pending instead, confirm an incremental \$110,000.
- Submit Hot \$250,040 / Cold \$749,960 (25.004%) and confirm `SWEEP_TO_COLD` of \$100,040 — not `HOLD_BALANCES`.
- Submit `hot_wallet_usd=float("nan")` and confirm `HotColdWalletError` rather than `PORTFOLIO_BALANCED`; submit a negative balance and confirm the same.
- Set only `api_key_universal_transfer_enabled=True` and confirm `CRITICAL_SECURITY_ALERT` with `is_api_key_secure` `False`.
- Request a \$120,000 refill from a \$20,000 vault and confirm the proposal is capped at \$20,000 with `is_transfer_fully_fundable` `False`.
- Construct `HotColdWalletManagerEngine(target_hot_ratio=0.30, max_hot_ratio_threshold=0.25)` and confirm `HotColdWalletError`.
- Pass `pending_transfer_to_cold_usd` greater than `hot_wallet_usd` and confirm `HotColdWalletError` rather than a spurious refill in the opposite direction; likewise Hot \$1e308 / Cold \$1e308, which overflows the total.
- Run `python -m unittest discover -s skills/hot-cold-wallet-split-for-trading-bots/scripts` and confirm a 100% pass rate.

## Related Skills

- `crypto-wallet-key-custody-security`
- `exchange-withdrawal-whitelist-enforcement`
- `api-key-least-privilege-audit-tool`
- `multi-signature-approval-for-large-transfers`
- `hardware-security-module-hsm-for-signing-keys`
- `test-transaction-verification-before-large-transfers`
