# Checklist for B3 PUMA Integration

## Scope acknowledgement (read first)
- [ ] Understood that this module performs **no network I/O** — `connect()` without an injected connector validates configuration only
- [ ] No health check, monitor, or go-live gate treats `connect() == True` as proof of reachability or valid credentials
- [ ] A real connector (vendor SDK or own socket layer) is injected for any live session

## Protocol selection
- [ ] Protocol suite chosen deliberately, not by default
- [ ] If `MODERN_BINARY_SBE`: confirmed the strategy can work with **MBO only** — Binary UMDF provides no MBP and no Top of Book
- [ ] If `LEGACY_FIX_FAST`: confirmed current FIX order entry gateway availability with B3, given the phased gateway reduction (Derivatives Q4 2025, Equities 10 Apr – 15 May 2026)
- [ ] Any B3-assigned new IPs, ports, or TargetCompIDs applied before the assigned cutoff — sessions fail to log in otherwise

## Credentials and configuration
- [ ] SenderCompID / TargetCompID obtained from B3 Membership Services
- [ ] **Permitted CompID format confirmed with B3** — the built-in `^[A-Za-z0-9_]{1,12}$` whitelist is this skill's defensive default, not a published B3 grammar; `comp_id_pattern` overridden if B3's rule differs
- [ ] Password sourced from a secrets manager or environment variable, never hard-coded or committed
- [ ] Config objects are safe to log (`password` is `repr=False`); no code path logs `config.password` directly
- [ ] `order_entry_ip` is a unicast dotted-quad string
- [ ] `market_data_multicast_ip` is a genuine multicast address; `require_multicast_market_data=False` used only for an isolated test rig
- [ ] IP whitelisting confirmed with B3 for both order entry and market data

## Gap recovery
- [ ] For `MODERN_BINARY_SBE`: `enable_application_gap_recovery=True` reflects an **implemented** recovery path, not a flag flipped to satisfy the validator
- [ ] Sequence numbers tracked on **every** received market data message
- [ ] Gap detection fires at receive time (expected ≠ received), not on a timer or by noticing a stale book
- [ ] Recovery uses the **snapshot/refresh** stream with RptSeq/LastRptSeq resynchronisation — **not** a TCP gap-fill request, which Binary UMDF does not provide
- [ ] Channel Reset and EmptyBook events handled
- [ ] Incrementals arriving during recovery are buffered and replayed after resync
- [ ] For `LEGACY_FIX_FAST`: decided which of TCP Replayer, TCP Historical Replayer, or Snapshot Recovery applies to which loss scale

## Implementation verification
- [ ] `B3ConnectionConfig` rejects malformed CompIDs, including special and control characters
- [ ] `B3ConnectionConfig` rejects integer and malformed IP addresses
- [ ] `B3ProtocolSuite.MODERN_BINARY_SBE` with `enable_application_gap_recovery=False` raises `B3ConfigurationError`
- [ ] `B3ProtocolSuite.LEGACY_FIX_FAST` permits `enable_application_gap_recovery=False`
- [ ] State transitions verified: DISCONNECTED → CONNECTING → CONNECTED → DISCONNECTED
- [ ] A failing connector produces `FAILED` state and raises `B3ConnectionError`
- [ ] Retry-after-failure works (`connect()` callable again from `FAILED`)
- [ ] `disconnect()` reaches DISCONNECTED even when the disconnector raises

## Testing
- [ ] Run `python -m unittest discover -s skills/b3-brazil-exchange-api-integration/scripts` — 29 tests, all pass
- [ ] Certification environment: FIXP Negotiate/Establish (Binary) or FIX logon (Legacy) completes
- [ ] Multicast joins succeed and messages decode against official B3 schemas
- [ ] Induced packet loss is detected and recovered via the correct mechanism for the feed
- [ ] Invalid credentials and unreachable endpoints surface as `FAILED`, not silent success

## Production deployment
- [ ] Firewall rules validated: TCP (order entry) and UDP multicast (market data)
- [ ] Retry policy configured with exponential backoff and a maximum attempt cap
- [ ] Logging captures connection lifecycle events with timestamps for audit
- [ ] Alerting configured for `FAILED` transitions and market data sequence gaps
- [ ] Market data licensing and redistribution terms confirmed with B3
- [ ] Runbook created for connection troubleshooting and feed resynchronisation
- [ ] Latency baselined **by measurement in your own footprint** — this skill quotes no latency figures because none could be sourced

## Post-deployment
- [ ] Connection stability monitored across pre-open, open, close, and post-close
- [ ] Sequence-gap and recovery events reviewed after each session
- [ ] Order submission and execution reporting verified end to end
- [ ] Resource cleanup verified on shutdown

## Sign-off
- Connectivity Engineer: ___________________________
- Date: ___________________________
- Protocol facts re-verified against current B3 specifications: ___________________________
- Security review (credential handling): ___________________________
