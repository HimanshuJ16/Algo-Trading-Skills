# Deep Workflow Reference — network-interface-level-tick-timestamping

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Enable Socket Options**:
   - Enable `SO_TIMESTAMPNS` or `SO_TIMESTAMPING` on UDP/TCP socket.

2. **Receive Ancillary Packet Control Data**:
   - Execute `recvmsg()` to receive socket payload and control message buffers.

3. **Decode Hardware Timespec**:
   - Unpack struct `timespec` (`sec`, `nsec`) from `cmsg_data`.

4. **Compute Kernel Queueing Jitter**:
   - Calculate $\Delta t_{\text{kernel}} = T_{\text{app}} - T_{\text{hardware}}$ and assign $T_{\text{hardware}}$ to market tick object.

## Production Implementation Reference

- Reference code: `scripts/nic_timestamping.py` (`NICHardwareTimestamperEngine`, `TimestampSource`, `NICTimestampedPacket`).
- Automated unit tests: `scripts/test_nic_timestamping.py`.
