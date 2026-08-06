# Institutional Crypto Custody & Withdrawal Velocity Standards

## 1. Withdrawal Security Threshold & Decision Matrix
| Velocity / Risk Category | Condition Formula | Operational Action | Required Timelock / Escrow |
| :--- | :--- | :--- | :--- |
| **Normal Compliant** | $V_{\text{1h}} \le L_{\text{1h}} \ \land \ Z_{\text{size}} < 3.0 \ \land \ t_{\text{addr\_age}} \ge 24\text{h}$ | **APPROVED** | 0 Hours (Automated Processing) |
| **Account Hourly Limit Breached** | $V_{\text{1h}} + A_{\text{req}} > L_{\text{1h}} \ ( \$100\text{k} )$ | **TIMELOCK_HOLD** | 24-Hour Cooling-Off Period |
| **Account Daily Limit Breached** | $V_{\text{24h}} + A_{\text{req}} > L_{\text{24h}} \ ( \$500\text{k} )$ | **TIMELOCK_HOLD** | 24-Hour Cooling-Off / Multi-Sig |
| **Anomaly Size Outlier** | $Z_{\text{size}} = \frac{A_{\text{req}} - \mu_{90\text{d}}}{\sigma_{90\text{d}}} \ge 3.0$ | **TIMELOCK_HOLD** | Step-Up 2FA & Manual Review |
| **New Destination Address** | $t_{\text{now}} - t_{\text{whitelist\_added}} < 24.0\ \text{hours}$ | **TIMELOCK_HOLD** | 24-Hour Whitelist Cooling |
| **Hot Wallet Circuit Breaker** | $V_{\text{global\_1h}} + A_{\text{req}} > L_{\text{global\_1h}} \ ( \$2\text{M} )$ | **REJECTED_FREEZE** | **Automated Hot Wallet Freeze** |

---

## 2. Quantitative Velocity & Anomaly Formulas

### A. Rolling Window Velocity ($V_{\text{rolling}}$):
$$V_{\text{rolling}}(T_{\text{hours}}) = \sum_{i \in \text{Withdrawals in } [t_{\text{now}} - T,\; t_{\text{now}}]} A_{\text{usd}, i}$$

### B. User Historical Anomaly Z-Score ($Z_{\text{size}}$):
$$Z_{\text{size}} = \frac{A_{\text{req}} - \mu_{90\text{d}}}{\sigma_{90\text{d}}}$$

Where $\mu_{90\text{d}}$ and $\sigma_{90\text{d}}$ are the user's historical 90-day mean and standard deviation of USD withdrawal amounts.

---

## 3. Circuit Breaker Escalation Standard
1. **TIMELOCK_HOLD**: Automatically locks withdrawal for 24 hours. Sends push notification and email alert to account owner with cancellation link.
2. **REJECTED_FREEZE**: Immediately halts automated hot wallet disbursement queue. Dispatches PagerDuty alert to Security Operations Center (SOC) and key custodians.