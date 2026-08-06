# Institutional Market Abuse Surveillance Operations Checklist

## Pre-Trade Controls & Self-Match Prevention (SMP)
- [ ] **Beneficial Ownership Grouping**: Map all sub-accounts, desks, and trader IDs to master beneficial entity IDs.
- [ ] **SMP Mechanism Configuration**: Enable Exchange Self-Match Prevention (Cancel Incoming / Cancel Resting / Cancel Both).
- [ ] **Wash Trade Window Setting**: Set self-cross matching window ($\Delta t \le 2.0\ \text{seconds}$).

## Real-Time Spoofing & Layering Monitoring
- [ ] **Order Cancellation Ratio Limits**: Configure automated alerts for traders exceeding $90\%$ order cancellation ratios.
- [ ] **Order Lifespan Tracking**: Monitor order cancellations with lifespans $< 1,000\ \text{ms}$.
- [ ] **Opposite-Side Fill Pattern Analysis**: Flag fills occurring simultaneously with large opposite-side order cancellations.

## Compliance Auditing & Escalation
- [ ] **MiFID II RTS 6 / CAT Audit Logging**: Retain microsecond-timestamped order event logs (`PLACE`, `CANCEL`, `FILL`) for 5 years.
- [ ] **Algorithmic Execution Pause**: Automatically disable execution algorithms flagged for `CRITICAL` market abuse violations.
- [ ] **Compliance Officer Alert Escalation**: Route automated alert emails and log entries to Chief Compliance Officers.