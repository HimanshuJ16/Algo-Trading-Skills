# Workflows for PTP Clock Synchronization

1. **Interface Audit**:
   - Check NIC PTP hardware timestamping support:
     ```bash
     ethtool -T eth0
     ```
     Ensure `SOF_TIMESTAMPING_RAW_HARDWARE` and `SOF_TIMESTAMPING_TX_HARDWARE` are listed.

2. **`ptp4l` Daemon Launch**:
   - Execute `ptp4l` with hardware timestamping (`-H`), Layer-2 transport (`-2`), slave-only (`-s`):
     ```bash
     ptp4l -i eth0 -2 -H -s -m
     ```

3. **`phc2sys` Daemon Launch**:
   - Synchronize `CLOCK_REALTIME` to the PHC device `/dev/ptp0`:
     ```bash
     phc2sys -s /dev/ptp0 -c CLOCK_REALTIME -w -m
     ```

4. **Telemetry Ingestion**:
   - Route stdout/stderr or log files from both daemons to `PtpClockSyncManager`.
   - Extract metrics: `rms_offset_ns`, `max_offset_ns`, `ptp_state`, `grandmaster_id`.

5. **Compliance & Alerting**:
   - Trigger warning if RMS offset > 500ns.
   - Trigger critical alert / kill-switch if state != `s2` (or `SLAVE`) or max offset > 100,000ns (100µs MiFID II limit).
