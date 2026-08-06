# Institutional Withdrawal Velocity & Security Operations Checklist

## Velocity Limit & Threshold Configuration
- [ ] **Account Rolling Velocity Limits**: Set account 1-hour ($100k) and 24-hour ($500k) rolling USD velocity thresholds.
- [ ] **Global Hot Wallet Limit**: Configure global hot wallet 1-hour rolling USD ceiling ($2.0M).
- [ ] **USD Oracle Integration**: Feed real-time asset FX rates to convert all crypto withdrawals to USD equivalent.

## Anomaly Detection & Address Whitelisting
- [ ] **User Profile Baseline Calculation**: Track 90-day user historical withdrawal mean ($\mu$) and standard deviation ($\sigma$).
- [ ] **Z-Score Anomaly Threshold**: Flag transactions with $Z_{\text{amount}} \ge 3.0$ for manual step-up verification.
- [ ] **Address Whitelist Cooling Period**: Enforce mandatory 24-hour cooling-off period for newly added destination addresses.

## Automated Circuit Breakers & Incident Escalation
- [ ] **Automated Timelock Escrow**: Place flagged withdrawals into a 24-hour `TIMELOCK_HOLD` state.
- [ ] **Hot Wallet Freeze Integration**: Instantly halt HSM signer broadcasts if global hot wallet velocity is breached.
- [ ] **SOC Incident Escalation**: Dispatch high-priority PagerDuty alerts to Security Operations Center (SOC) on `REJECTED_FREEZE`.