# Withdrawal Velocity Limits — Standards and Sourced Parameters

## 1. Status of these requirements

**No regulator prescribes a withdrawal velocity number.** There is no rule that
says $100k per hour, $2M per hot wallet, or 24 hours of address cooling. Every
threshold in this skill is an **engineering placeholder** to be calibrated
against your own flow.

What regulators do address is the *control class* — that abnormal withdrawal
activity must be detected, that limits must exist and be "appropriate", and that
alerts must escalate. The distinction matters: a compliance write-up that cites a
regulator as the source of a specific number is misinformation.

| Source | Reference | What it actually says | Applicability |
| :--- | :--- | :--- | :--- |
| HK SFC | Circular **SFO/IS/005/2026**, 11 Feb 2026, para 20 | VA brokers permitting client withdrawals should collaborate with VATP operators "to strengthen abnormal withdrawal detection, such as by setting **appropriate** withdrawal limits at the omnibus account level or for **newly whitelisted client wallets**, blocking suspicious withdrawal attempts, and ensuring timely escalation to the VA broker". Also expects "continuous monitoring with effective incident escalation and suspension mechanisms during and outside normal office hours", and systems "sufficiently robust to prevent, detect and respond to unauthorised attempts to initiate client withdrawals". | Hong Kong SFC-licensed corporations providing VA dealing services under an omnibus arrangement. **Names the controls, sets no numbers** — "appropriate" is left to the firm. This is the closest thing to a regulatory hook for this skill, and it maps onto all three of its layers: aggregate limits, newly-whitelisted-address treatment, and escalation/suspension. |
| HK SFC | Circular **SFO/IS/025/2025**, 15 Aug 2025 (custody of virtual assets) | "destination addresses for client withdrawal instructions cannot be modified before the transactions are signed and broadcast to the respective blockchain." Also requires real-time reconciliation of on-chain client assets against ledger balance, with prompt SOC alerting on discrepancies. | HK SFC-licensed VATP operators. This is the requirement behind the engine's address-binding check: an approval that is not bound to the exact destination it was granted for does not satisfy it. |

**Everything else is firm policy.** If you need a second jurisdiction's position,
verify it against that regulator's own text before citing it here — do not carry
these HK citations across borders.

### What this skill is *not* evidence of

Velocity caps here are a **security** control against credential compromise. They
are not AML transaction monitoring, and the thresholds are not
suspicious-activity or Travel Rule thresholds. Those regimes have their own
triggers and their own numbers; conflating them produces a control that satisfies
neither.

## 2. Engine defaults — engineering policy, not standards

| Parameter | Default | Basis |
| :--- | :--- | :--- |
| `account_hourly_limit_usd` | 100,000 | Placeholder. **No regulatory basis.** Calibrate to the account tier's observed flow. |
| `account_daily_limit_usd` | 500,000 | Placeholder. **No regulatory basis.** Validated as `>= hourly`, or the daily cap could never bind. |
| `global_hot_wallet_hourly_limit_usd` | 2,000,000 | Placeholder. **No regulatory basis.** Should relate to the hot wallet float you are willing to lose in one hour, not to a round number. |
| `anomaly_zscore_threshold` | 3.0 | Conventional outlier cut-off. Not a false-positive guarantee — see §3. |
| `min_profile_observations` | 30 | Engineering floor for a usable sigma. Constrained by the Grubbs bound in §3. |
| `address_whitelist_cooling_hours` | 24.0 | Placeholder. **Not a standard** — venue practice ranges 12–72h. See `exchange-withdrawal-whitelist-enforcement/references/standards.md`, which records the per-venue spread. |
| `timelock_hold_hours` | 24.0 | Placeholder; should exceed your actual review SLA. |
| `max_clock_skew_seconds` | 300.0 | Warning threshold only; never changes a decision. |

## 3. The Z-score is a ranked outlier score, not a probability

`Z = (A_req - mu) / sigma` where `mu`, `sigma` are the account's own historical
mean and standard deviation over a stated lookback.

**Attainability (Grubbs bound).** For a sample of size `n`, when `mu` and `sigma`
are that sample's own mean and (n-1 denominator) standard deviation, no single
in-sample observation can exceed

```
max |Z| = (n - 1) / sqrt(n)
```

| n | max in-sample \|Z\| |
| :--- | :--- |
| 5 | 1.79 |
| 10 | 2.85 |
| 11 | 3.02 |
| 30 | 5.29 |

So a `Z >= 3.0` rule on a 5-observation profile is **incoherent**: no withdrawal
the account ever made could have scored above 1.79, meaning the threshold was
never calibrated against observed behaviour. `Z = 3.0` first becomes attainable
in-sample at `n = 11`. `WithdrawalVelocityEngine.__init__` refuses a
threshold/`min_profile_observations` pair that fails this test, rather than
shipping a rule that cannot fire. (The bound constrains points *inside* the
sample; a new request is out-of-sample and may exceed it, but a sigma estimated
from a handful of points is too unstable for the excess to carry meaning.)

**Distributional caveat.** Withdrawal sizes are right-skewed and heavy-tailed,
not Gaussian. "3 sigma" therefore does **not** imply a ~0.13% one-sided false
positive rate. Calibrate the threshold against your realised alert volume, or
score `log(amount)` if you want the normal approximation to be less wrong.

**The rule is one-sided by design.** Only `Z >= threshold` flags. An unusually
*small* withdrawal is not a draining signal, and flagging it would bury the
analyst in noise. Note the corollary: an attacker who keeps each withdrawal near
the account's historical mean is invisible to this layer — the rolling velocity
caps, not the Z-score, are what catch that.

**Baseline poisoning.** If `mu` and `sigma` are recomputed from a window that
includes an attacker's own recent withdrawals, the baseline drifts toward the
attack. Compute the profile from a lookback that lags the current window, and
exclude amounts that were themselves flagged.

## 4. Vendor mapping — configure the equivalent policy at the custodian

This engine gates *your* code. An attacker with your custodian credentials never
executes it. The equivalent server-side control, where the vendor offers one:

| Vendor | Mechanism | Verified detail |
| :--- | :--- | :--- |
| **Fireblocks** | Transaction Authorization Policy (TAP) | A rule takes `amount`, `amountCurrency` (`USD`, `EURO`, or `NATIVE`), and `amountScope` — `SINGLE_TX` for a per-transaction ceiling or `TIMEFRAME` for a cumulative limit over `periodSec` seconds. Actions are `ALLOW`, `BLOCK`, or `2-TIER` (additional approval). Rules resolve on a **first-match** principle, so ordering is part of the policy. `amountCurrency: USD` is what makes a fiat-denominated cap possible at the vendor. **Not verified:** the docs describe TIMEFRAME aggregation as accumulating "until the total exceeds the value you specify" but do **not** state whether the period is a rolling window or a fixed bucket — confirm with Fireblocks before assuming your local rolling semantics match theirs. |
| **BitGo** | Wallet policy rules | A **Velocity Limit** rule type constrains how much is withdrawn from a wallet over a time window; other rule types include destination, initiator, percentage-of-wallet-balance, threshold, and webhook. Policy outcomes are automatic approve/deny or requiring a second approval. **Not verified:** exact parameter names and window semantics are not given in the policies overview — read the API reference for the wallet type you actually use. |
| **Coinbase Prime / Anchorage** | Vendor-managed withdrawal controls and approval quorums | **Not independently verified for this skill.** Treat the frontmatter listing as "this skill's pattern is relevant to these platforms", not as a claim about specific configurable fields. Confirm against current vendor documentation before relying on any parameter. |

The takeaway from the spread — per-transaction vs. cumulative, rolling vs.
unstated, block vs. escalate-for-approval — is that a local cap and a vendor cap
are **not** interchangeable, and the two can disagree about whether a given
request breached a limit. Configure both, and reconcile which one you treat as
authoritative.

## 5. Escalation semantics

| Decision | Meaning | Required follow-through |
| :--- | :--- | :--- |
| `APPROVED` | Within every cap; consumed velocity capacity at the trusted clock. | Route to signer. |
| `TIMELOCK_HOLD` | One or more risk flags. Funds have **not** moved and no capacity was consumed. | Human review. On release, `release_held_withdrawal(...)` so the amount counts toward velocity; on rejection, `cancel_held_withdrawal(...)`. A `WHITELIST_RECORD_MISMATCH` flag is an integration fault or tampering — investigate the lookup path before releasing anything. |
| `REJECTED_FREEZE` | Global hot-wallet cap breached. The breaker is **latched**. | Halt the automated disbursement queue, page the SOC, and investigate. The only exit is `reset_hot_wallet_freeze(authorized_by=...)`, which records who re-armed it. Aligns with the SFC's expectation of "suspension mechanisms" and timely escalation (§1). |
