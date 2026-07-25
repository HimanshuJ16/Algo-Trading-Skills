# Best Execution & Record-Keeping Checklist

## Technical Requirements
- [ ] Precision timestamping applied (minimum milliseconds, preferably micro/nano).
- [ ] UTC timezone enforced across all systems.
- [ ] Cryptographic hash generation implemented for each trade record.
- [ ] Data validation for required fields (Order ID, Symbol, Quantity, Price, Fills).

## Compliance & Regulatory
- [ ] Slippage bounds defined and enforced against benchmarks.
- [ ] MiFID II / SEC specific tags are captured correctly.
- [ ] Client constraints and instructions logged.
- [ ] Automated alerts configured for slippage tolerance breaches.

## Audit & Reporting
- [ ] Audit log export format defined (JSON, CSV).
- [ ] Log storage meets WORM (Write-Once-Read-Many) requirements.
- [ ] System handles missing timestamps gracefully, generating compliance alerts.