# Pre-Flight Checklist

- [ ] Has NIC hardware timestamping capability been verified using `ethtool -T`?
- [ ] Is `ptp4l` configured with `-H` (Hardware Timestamping) and `-2` (Layer 2)?
- [ ] Is `phc2sys` running to sync the Linux system clock (`CLOCK_REALTIME`) to `/dev/ptp0`?
- [ ] Is `PtpClockSyncManager` parsing log telemetry to detect offset spikes > 100,000ns?
