# TASE Production Integration & Deployment Checklist

## Pre-Flight Configuration Verification
- [ ] **Target Environment**: Confirm FIX host/port configured for TASE Production FIX Gateway vs UAT Simulator.
- [ ] **Credentials**: Verify `SenderCompID`, `TargetCompID`, `TraderID`, and Account numbers issued by TASE Member Services.
- [ ] **Network & VPN**: Verify persistent IPSec VPN or Direct Co-Location line to TASE primary datacenter with active failover route.
- [ ] **ISIN & Security Master**: Populate local security master from TASE Data Hub with valid ISINs and 6/7-digit TASE Security Numbers.

## Price & Currency Denomination Audits
- [ ] **Agorot Conversion Verification**: Confirm equity & ETF order entry pipelines convert ILS model prices to Agorot (x100) before message serialization.
- [ ] **Bonds & Makam Par Scaling**: Confirm bond order entry pipelines format limit prices as percentage of par value (e.g. 101.25%).
- [ ] **Tick Size Alignment**: Verify limit prices conform to dynamic TASE tick size bands per price level.

## Pre-Trade Risk & Regulatory Compliance
- [ ] **Max Order Value Limit**: Configure `max_order_value_ils` cap per single order.
- [ ] **Max Order Quantity Limit**: Configure `max_order_qty` cap per single order.
- [ ] **Dynamic Price Collar**: Enforce dynamic collar check (e.g. ±10%) against TASE reference price to prevent market impact rejections.
- [ ] **Order-to-Trade Ratio (OTR)**: Verify rate limiter / order throttling module is active to comply with ISA regulations.
- [ ] **Self-Match Prevention**: Populate `TraderID` / `SubID` tags to prevent cross-account internal matching.

## Calendar & Schedule Alignment
- [ ] **Sunday Trading Calendar**: Enable Sunday session schedule in algorithm scheduler.
- [ ] **Israel Weekend (Fri-Sat)**: Ensure execution engines bypass order generation on Friday and Saturday.
- [ ] **Timezone Alignment**: Verify server clock synchronization (PTP/NTP) with Israel Standard Time (IST).

## Failover & Operational Recovery
- [ ] **Intraday Sequence Number Persistence**: Verify FIX sequence numbers persist to database/disk across bot restarts.
- [ ] **Emergency Kill-Switch**: Test manual emergency cancel-all call (`cancel_order()` loop) to drain open orders instantly.
- [ ] **Post-Trade Execution Reconciliation**: Run unit tests (`python -m unittest discover -s skills/tase-israel-exchange-api/scripts`) and verify VWAP execution report tracking.

