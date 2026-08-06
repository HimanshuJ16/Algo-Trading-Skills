---
name: withdrawal-velocity-limits-and-anomaly-detection
description: "Institutional crypto custody security skill for enforcing multi-window rolling withdrawal velocity caps (1-hour, 24-hour) per account and global hot wallet, calculating 90-day user historical anomaly Z-scores, verifying destination address whitelisting age, and executing automated circuit breakers (APPROVED, TIMELOCK_HOLD, REJECTED_FREEZE)."
domain: Crypto Custody & Exchange Risk Engineering
subdomain: Automated Hot Wallet Security & Velocity Controls
tags:
- crypto
- custody
- security
- velocity-limits
- anomaly-detection
- hot-wallet-freeze
- timelock-escrow
- z-score
brokers_frameworks:
- bitgo
- fireblocks
- coinbase-custody
- anchorage
- custom-hsm
version: 1.1.0
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when designing crypto exchange hot wallet withdrawal gateways, institutional custody platforms, or prime brokerage settlement systems to prevent API key compromise, hot wallet draining attacks, or unauthorized insider exfiltration.

This skill provides institutional mechanisms to:
- Calculate **Rolling Hourly and Daily USD Withdrawal Velocity** per account ($V_{\text{1h}}, V_{\text{24h}}$).
- Enforce **Global Hot Wallet Circuit Breakers** ($V_{\text{global\_1h}}$) to instantly freeze automated disbursements during high-volume draining attacks.
- Derive **User Historical Anomaly Z-Scores** ($Z = \frac{A_{\text{req}} - \mu_{90\text{d}}}{\sigma_{90\text{d}}}$).
- Enforce **Destination Address Whitelist Cooling Periods** (24-hour mandatory delay for new addresses).
- Output automated risk decisions (`APPROVED`, `TIMELOCK_HOLD`, `REJECTED_FREEZE`) with specific risk flags.

## Prerequisites

- Python 3.9+
- Standard Python libraries (`datetime`, `dataclasses`, `math`, `typing`).
- Historical user withdrawal profile ($\mu$, $\sigma$, historical count) and destination address whitelist database.

## Workflow

1. **Construct Withdrawal Request**: Define `WithdrawalRequest` specifying request ID, account ID, asset, amount crypto, amount USD equivalent, destination address, and microsecond timestamp.
2. **Retrieve Whitelist & Profile Records**: Fetch `AddressWhitelistRecord` (address, added timestamp) and `AccountHistoricalProfile` (90-day mean USD, std dev USD).
3. **Execute Hot Wallet Circuit Breaker Check**: Call `evaluate_withdrawal_request(req, whitelist, profile)` to test global hot wallet 1-hour rolling velocity limits. If exceeded, return `REJECTED_FREEZE`.
4. **Evaluate Account Velocity & Anomaly Z-Score**: The engine calculates account rolling 1h/24h totals and evaluates whether $Z_{\text{amount}} \ge 3.0$ or address age $< 24\ \text{hours}$.
5. **Issue Risk Decision & Ledger Update**: If risk flags exist, apply a 24-hour `TIMELOCK_HOLD`. If compliant, return `APPROVED` and record the transaction into the rolling velocity ledger.

## Common Pitfalls

- **Static Non-Rolling Velocity Windows**: Resetting velocity limits at midnight UTC allows attackers to withdraw $100\%$ of daily capacity at 23:59 and another $100\%$ at 00:01. Always use **Continuous Rolling Windows** (e.g. past 60 minutes, past 1,440 minutes).
- **Ignoring Asset FX Volatility**: Calculating velocity limits purely in crypto units (e.g. 10 BTC) exposes the firm to fiat market spikes. Velocity limits MUST be evaluated in **USD Equivalent Value** using real-time oracle prices.
- **Immediate Execution on New Addresses**: Permitting instant withdrawals to newly added destination addresses invalidates 2-Factor Authentication security. Enforce a mandatory **24-Hour Cooling-off Period** for new whitelist entries.
- **No Global Hot Wallet Cap**: Protecting individual user accounts without a global hot wallet circuit breaker allows an attacker compromising multiple accounts to drain hot wallet funds simultaneously.

## Verification

Run the unit test suite to validate compliant withdrawals, hourly velocity breaches, daily velocity breaches, Z-score anomaly flags ($Z \ge 3.0$), newly whitelisted address timelock holds, and global hot wallet circuit breaker freezes:

```bash
python -m unittest discover -s skills/withdrawal-velocity-limits-and-anomaly-detection/scripts
```

## Related Skills

- `test-transaction-verification-before-large-transfers`
- `third-party-custody-audit-report-review-cadence`
- `vendor-lock-in-risk-for-proprietary-custody-formats`
- `wash-trade-and-spoofing-self-detection`
