# Pre-Flight / Sign-off Checklist — network-interface-level-tick-timestamping

Use this before considering the skill's implementation complete.

- [ ] **Socket Option Activation:** Confirm `SO_TIMESTAMPNS` or `SO_TIMESTAMPING` is set on socket.
- [ ] **Ancillary Control Buffer Parsing:** Confirm `recvmsg` control buffers are unpacked correctly.
- [ ] **Timespec Nanosecond Precision:** Confirm nanoseconds are extracted without floating point truncation.
- [ ] **Application Fallback Guard:** Confirm process falls back gracefully if NIC timestamps are unavailable.
- [ ] **Automated Testing:** Run `python scripts/test_nic_timestamping.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
