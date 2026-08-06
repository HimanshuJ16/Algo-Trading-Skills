# Institutional Market Data Vendor Fallback Operations Checklist

## Multi-Vendor Hierarchy Setup
- [ ] **Priority Assignment**: Assign explicit priority ranks (Priority 1 = Direct Feed, Priority 2 = Aggregator, Priority 3 = REST API).
- [ ] **Staleness Threshold Configuration**: Configure `max_staleness_seconds` per vendor (e.g. 2s for direct, 5s for aggregator).
- [ ] **Error Threshold Calibration**: Set `max_error_threshold` (e.g. 3 consecutive socket disconnects or timeouts) before marking node as `ERROR`.

## Failover Execution & Anti-Flapping
- [ ] **Automated Failover Drill**: Test seamless failover from Priority 1 to Priority 2 during simulated Primary feed disconnection.
- [ ] **Anti-Flapping Recovery Cooling**: Verify mandatory recovery cooling period (e.g. 30 seconds) prevents rapid oscillation back to Primary.
- [ ] **Synthetic Cache Seeding**: Confirm live ticks populate local synthetic cache for emergency fallback.

## Synthetic Fallback & Audit Trail
- [ ] **Synthetic Quote Flagging**: Ensure execution engines check `is_synthetic=True` and apply wider spread limits when operating on cached ticks.
- [ ] **Failover Event Logging**: Verify all failover events are persisted to audit logs for post-trade SLA compliance review.

