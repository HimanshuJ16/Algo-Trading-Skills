# Workflows for Hot/Cold Wallet Allocation

## 1. Configure the band once, at construction

Choose `target_hot_ratio`, `max_hot_ratio_threshold`, and
`min_hot_ratio_threshold` from actual working-capital needs — how much the bot
must be able to deploy between refill cycles — not from the defaults. Set
`regulatory_max_hot_ratio` if a mandated ceiling binds you (see
`standards.md`). The engine rejects a band that cannot converge:
`min < target < max`, and the mandated cap must sit strictly above the floor.

## 2. Snapshot balances at one consistent mark

Convert every asset to USD at a single timestamp. The engine has no price
source and cannot detect a stale or mixed-timestamp mark; it will compute a
ratio from whatever it is given. Include the warm buffer — it is part of the
denominator, and it is echoed back in the report so `hot + cold + warm` always
reconciles to `total_portfolio_usd`.

## 3. Read the trading key's live permission set

Query the exchange rather than assuming the scope the key was created with.
For Binance, `GET /sapi/v1/account/apiRestrictions` returns
`enableWithdrawals`, `enableInternalTransfer`, `permitsUniversalTransfer`, and
`ipRestrict`. Map all four onto `WalletBalances`. Leave
`api_key_ip_restricted` as `None` only if you genuinely did not check — the
report will say so rather than implying the key is network-scoped.

## 4. Supply in-flight transfers

Pass every submitted-but-unsettled movement through
`pending_transfer_to_cold_usd` / `pending_transfer_to_hot_usd`. This is what
makes a scheduled audit idempotent. Track them from your own transfer ledger,
keyed by the transfer's client-side identifier, and clear each one only when
the transaction is confirmed to the depth your policy requires — not when it is
broadcast.

## 5. Evaluate

`audit_and_rebalance_treasury` validates inputs, nets in-flight transfers,
audits key permissions, and returns a `HotColdWalletAuditReport`. It raises
`HotColdWalletError` on non-finite, negative, or empty balances rather than
returning a verdict built on them.

## 6. Route the outcome

| Status | Meaning | Action |
|---|---|---|
| `CRITICAL_SECURITY_ALERT` | A fund-moving permission is enabled on the trading key. | Halt trading and revoke the permission. Rebalancing is suppressed and the proposal is \$0 — do not route treasury movements through a key you are about to revoke. The observed ratio is still reported, so a concurrent exposure breach stays visible. |
| `REGULATORY_HOT_CAP_BREACH` | The effective hot ratio exceeds a mandated ceiling. | Escalate to compliance as well as ops. Logged at `CRITICAL`. |
| `REBALANCE_REQUIRED` | Ordinary band breach. | Execute the proposed sweep or refill. |
| `PORTFOLIO_BALANCED` | Within band after netting in-flight transfers. | No action. |

Always check `is_transfer_fully_fundable`. When it is `False` the proposal was
capped by what the funding wallet actually holds, and the underlying imbalance
is *not* resolved by executing it — the shortfall needs a separate decision
(top up the vault, or accept a lower hot balance).

## 7. Execute idempotently, then feed the result back

The engine proposes; it does not execute. The executor owns idempotency: attach
a client-side transfer identifier, and never retry a transfer solely because a
request timed out — the venue may already have accepted it. Record the transfer
in your ledger before broadcasting, surface it as `pending_transfer_*` on the
next audit, and clear it on confirmation.

## 8. Log the report

Persist the full report, including `current_hot_ratio`, `effective_hot_ratio`,
the threshold band it was evaluated against, and `security_findings`. A report
that records only the decision cannot later answer why the decision was made.
