# Institutional UK FCA RTS 6 & FG18/9 Compliance Checklist

## Pre-Trade Control Configuration & Setup
- [ ] **Price Collar Calibration (RTS 6 Art 13(1))**: Set maximum allowed price deviation (e.g. 2.5%) against live NBBO midpoint.
- [ ] **Maximum Notional & Volume Caps (RTS 6 Art 13(2))**: Configure max single order value cap (£500,000) and max share volume cap (10,000).
- [ ] **Order-to-Trade Ratio (OTR) Limits (RTS 6 Art 13(3))**: Set OTR warning thresholds (max 100 quotes per execution) to prevent venue order spamming.
- [ ] **Credit Line Integration (RTS 6 Art 13(4))**: Integrate clearing broker credit line monitoring to reject orders exceeding available limits.

## Emergency Kill Switch & System Stress Setup
- [ ] **Automated Kill Switch Enablement (RTS 6 Art 12)**: Implement event-driven `trigger_kill_switch()` linked to FIX Mass Cancel APIs.
- [ ] **System Capacity Throttling (RTS 6 Art 14)**: Set automated order throttling at 95% message throughput capacity utilization.
- [ ] **Kill Switch Reset Protocol**: Establish compliance sign-off workflow for `reset_kill_switch()`.

## Annual Audit & Compliance Governance
- [ ] **Real-Time Audit Trail Archival**: Log all pre-trade control decisions (`PASSED`, `REJECTED`, `THROTTLED`) with microsecond timestamps.
- [ ] **FCA RTS 6 Self-Assessment**: Conduct annual self-assessment signed off by SMF24 (COO) or SMF16 (Compliance Oversight).
- [ ] **5-Year Log Retention**: Ensure pre-trade check logs and Kill Switch activation records are retained for 5 years.