---
name: withdrawal-velocity-limits-and-anomaly-detection
description: >-
  Use when a system can move crypto out of a hot wallet without per-transfer human
  approval, scoring each request against rolling per-account and global velocity caps, a
  size baseline and destination address age.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: crypto-custody-security
  tags: crypto, custody, security, velocity-limits, anomaly-detection, hot-wallet-freeze, circuit-breaker, z-score
  brokers_frameworks: "Fireblocks TAP; BitGo Wallet Policies; Coinbase Prime; Anchorage Digital; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when an automated system can move crypto out of a hot wallet without a human approving each transfer — an exchange withdrawal gateway, a treasury disbursement queue, or a prime-brokerage settlement job. It is the **amount and rate** layer of that gate: it answers "is this much, this fast, unusual for this account or for the hot wallet as a whole?" and returns a decision carrying the risk flags that produced it.

It exists because the controls that survive a stolen API key are the ones that bound *aggregate* damage. An attacker who passes authentication still has to move value, and value moved per unit time is the one signal a compromised credential cannot forge.

The engine enforces:
- **Rolling per-account 1h and 24h USD caps**, measured over continuous windows against a trusted clock.
- **A global hot-wallet 1h cap** that trips a **latching** circuit breaker — once tripped it stays tripped until `reset_hot_wallet_freeze(authorized_by=...)`.
- **A per-account size baseline** (`Z = (A_req - mu) / sigma`) that fails *closed* when the history is too thin for sigma to mean anything.
- **A cooling period on the destination address record**, after first verifying the record actually binds to this request's account and address.

## When NOT to Use

- **As the allowlist itself.** This engine does not own address registration, network scoping, memo binding, revocation, or address canonicalisation. It verifies the record it is handed and measures its age. Use `exchange-withdrawal-whitelist-enforcement` for the allowlist.
- **As the enforcer of last resort.** This runs in *your* infrastructure in front of *your* signer. An attacker holding your custodian API credentials calls the custodian directly and never executes this code. Configure the equivalent policy at the custodian too — the local gate is the second layer, not the first.
- **As AML transaction monitoring.** Velocity caps here are a security control against credential compromise. Suspicious-activity reporting, sanctions screening, and Travel Rule obligations are a separate regime with separate thresholds — see `sanctions-screening-for-counterparties-and-instruments` and `kyc-aml-considerations-for-algo-trading-entities`.
- **For a population of brand-new accounts.** A Z-score needs a baseline. With a thin profile the engine reports the check as not-run and holds; if most of your accounts are new, the anomaly layer holds nearly everything and you need a different segmentation.
- **As a durable ledger.** State is in memory and is not thread-safe. A restart resets every rolling window to zero — precisely the gap an attacker wants.

## Prerequisites

- Python 3.10+ and the standard library only (`datetime`, `dataclasses`, `enum`, `math`, `typing`, `logging`).
- A **trusted clock** for `evaluation_timestamp`. Pass it explicitly for reproducible audits; never let the request supply it.
- A **USD-equivalent valuation** for every request, from a price source you control. The caps are denominated in USD, so the oracle sits inside the trust boundary.
- Per-account `AccountHistoricalProfile` (`mean_withdrawal_usd`, `std_dev_usd`, `historical_count`) computed point-in-time over a stated lookback.
- Allowlist records carrying the `account_id` and `address` they belong to, so the binding check can run.
- Durable storage behind `withdrawal_ledger`, and serialisation around the evaluate-then-submit sequence.
- An operator path for both exits: `release_held_withdrawal` for an approved hold, `cancel_held_withdrawal` for a rejected one.

## Workflow

1. **Evaluate Against a Clock the Request Cannot Influence**: Pass `evaluation_timestamp`. Every window bound and every address age is measured from it. `WithdrawalRequest.timestamp` is client-asserted, recorded for the audit trail, and skew-checked into `decision.warnings` — never used as the clock. A velocity window you can slide by writing a number into the request is not a limit.
2. **Replay Before Scoring**: A gateway timeout means *unknown*, not *failed*. The engine caches the decision per `request_id` and replays it on retry. Without this, the retry of an approved withdrawal is scored a second time against a ledger the first attempt already updated — it double-counts velocity and can return a *different* decision than the one already acted on.
3. **Check the Latch, Then the Global Cap**: If the breaker is already latched, reject immediately. Otherwise test the global 1h hot-wallet total; breaching it latches the freeze and returns `REJECTED_FREEZE`. A breaker that re-arms itself when the rolling window decays is not a breaker — the attacker waits an hour and resumes.
4. **Score Per-Account Velocity, Then Size**: Test the rolling 1h and 24h account totals. Then compute `Z = (A_req - mu) / sigma`, but only when `historical_count >= min_profile_observations` and `sigma > 0`. When it cannot run, record `anomaly_zscore=None` and flag `INSUFFICIENT_PROFILE_HISTORY` — "did not run" is not "passed".
5. **Bind the Address Record Before Trusting Its Age**: Verify `whitelist_record.account_id` and `.address` match the request. A record is evidence about *one* (account, address) pair; measuring the age of a record fetched for some other address approves a withdrawal to an address nobody allowlisted. A record that does not bind is discarded as evidence, flagged `WHITELIST_RECORD_MISMATCH`, and held — never auto-released.
6. **Hold, or Approve and Consume Capacity**: Any flag produces `TIMELOCK_HOLD` and parks the request awaiting review. An approved request enters the ledger stamped with the *trusted clock*.
7. **Account for Released Holds**: A held withdrawal consumed no capacity because it moved no funds. When review releases one, call `release_held_withdrawal(request_id, authorized_by=...)` so it enters the ledger. Skip this and anyone who can get holds released has an unmetered channel straight through every rolling cap.

> Full procedure: see `references/workflows.md`.
> Sourced thresholds and vendor mapping: see `references/standards.md`.
> Printable checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Measuring the Window From the Request's Own Timestamp**: If velocity is computed from `request.timestamp`, anything that can build a request escapes every cap by claiming to be a week in the future. Use a clock the request cannot influence.
- **Leaving the Rolling Window Unbounded Above**: A window written as `entry.timestamp >= cutoff` with no upper bound counts a single future-dated entry toward *every* window until real time overtakes it — silently inflating velocity and eventually tripping the global breaker on legitimate flow. The window is `[now - T, now]`, closed at both ends.
- **Resetting Velocity at a Calendar Boundary**: A limit that resets at midnight UTC lets an attacker take 100% of the daily cap at 23:59 and 100% again at 00:01. Use continuous rolling windows.
- **Trusting a Whitelist Record Without Binding It to the Request**: Checking `record.is_whitelisted` and `record.added_timestamp` while never comparing `record.address` to `request.destination_address` approves a transfer to an arbitrary address whenever the caller's lookup is sloppy. Bind account *and* address, then measure age.
- **Letting NaN Decide**: `float('nan') >= 3.0` is `False`, so a NaN mean or sigma does not raise — it silently answers "not anomalous" and switches the anomaly control off while every dashboard still shows it enabled. Validate finite, and raise.
- **Treating a Thin Profile as a Pass**: Skipping the Z-score when `historical_count` is low fails *open* on exactly the accounts with no track record. Report the check as not-run and hold.
- **Setting a 3-Sigma Rule on a Handful of Observations**: With `n` observations and sigma estimated from them, no in-sample point can score above `(n-1)/sqrt(n)` — the Grubbs bound: 1.79 at n=5, 2.85 at n=10. A `Z >= 3.0` rule on a 5-observation profile was never calibrated against anything the account actually did; 3.0 first becomes attainable at n=11. The engine refuses that configuration at construction.
- **Assuming Withdrawal Amounts Are Normal**: Sizes are right-skewed and heavy-tailed, so a Z threshold does not deliver its Gaussian false-positive rate. Calibrate against your realised alert volume, or score `log(amount)`. Treat Z as a ranked outlier score, not a probability.
- **A Circuit Breaker That Self-Resets**: If the freeze lifts when the rolling window decays, the attacker pauses and continues. Latch it and require a named human to reset.
- **Forgetting That a Released Hold Moves Funds**: Holds sit outside the velocity ledger because they have not been disbursed. If a manual release does not feed back into the ledger, the released amount consumes no capacity.
- **Capping in Crypto Units Instead of USD**: A 10 BTC cap is a different amount of risk each week. Denominate in USD equivalent — and remember the price oracle then becomes a security dependency.
- **Losing the Ledger on Restart**: In-memory velocity state means a process restart zeroes every window, so a crash loop becomes a withdrawal window. Persist it.

## Verification

- Hand the engine a valid whitelist record for `0xSAFE` while the request's destination is `0xATTACKER`, and confirm `TIMELOCK_HOLD` with `WHITELIST_RECORD_MISMATCH` — not `APPROVED`. Repeat with a record whose `account_id` belongs to another account.
- Submit a profile with `mean=float('nan')` or `sigma=float('nan')` and confirm `VelocityEngineError`, not an approval. Same for `amount_usd=float('nan')`, `0.0`, and a blank destination address.
- Submit `historical_count=4` and confirm `TIMELOCK_HOLD` with `INSUFFICIENT_PROFILE_HISTORY` and `anomaly_zscore is None`. Repeat with `std_dev_usd=0.0`.
- Construct with `anomaly_zscore_threshold=3.0, min_profile_observations=5` and confirm `VelocityEngineError`; confirm `min_profile_observations=11` is accepted, and that `max_attainable_in_sample_zscore(5) == 1.7888543819998317`.
- Trip the global cap, then submit a small, whitelisted, in-baseline request 48 hours later — after every rolling window has decayed — and confirm it is still `REJECTED_FREEZE`. Call `reset_hot_wallet_freeze("soc@example.com")` and confirm the next request is `APPROVED`; confirm an empty authoriser raises.
- Evaluate the same `request_id` twice and confirm the identical decision object comes back, the ledger holds one entry, and velocity counted the amount once.
- Approve a request, then submit a second whose `timestamp` claims to be 10 days ahead, and confirm the earlier amount still counts toward the 1h window and a clock-skew entry appears in `warnings`. Confirm the ledger entry is stamped with the trusted clock, not the claimed time.
- Release a held withdrawal via `release_held_withdrawal` and confirm the amount then counts toward the rolling window and constrains the next request; confirm a cancelled hold can never be released.
- Run `python -m unittest discover -s skills/withdrawal-velocity-limits-and-anomaly-detection/scripts` and confirm a 100% pass rate.

## Related Skills

- `exchange-withdrawal-whitelist-enforcement`
- `multi-signature-approval-for-large-transfers`
- `hot-cold-wallet-split-for-trading-bots`
- `test-transaction-verification-before-large-transfers`
- `on-chain-transaction-monitoring-for-anomalies`
- `segregation-of-duties-for-custody-operations`
- `api-key-least-privilege-audit-tool`
- `kill-switch-and-drawdown-circuit-breakers`
