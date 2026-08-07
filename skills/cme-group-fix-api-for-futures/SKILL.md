---
name: cme-group-fix-api-for-futures
description: Quantitative market connectivity module for CME Group FIX API futures
  order entry and session management, implementing Tag 1028 (ManualOrderIndicator),
  Tag 7928/8000 (Self-Match Prevention), and MsgSeqNum gap-fill sequence recovery.
domain: Market Connectivity
subdomain: FIX Protocol
tags:
- cme-group
- fix-protocol
- futures
- tag1028
- smp
- self-match-prevention
- seqnum
brokers_frameworks:
- CME FIX 4.2
- CME FIX 4.4
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when implementing a direct FIX 4.2/4.4 protocol engine for CME Group futures. CME Globex FIX order entry requires specific mandatory fields—such as **Tag 1028 (ManualOrderIndicator)** and **Tag 7928/8000 (Self-Match Prevention)**—and strict sequence number (Tag 34) gap-fill recovery procedures to handle dropped packets or session restarts without order duplication.

## Prerequisites

- CME Globex SenderCompID, TargetCompID, and registered Operator IDs (Tag 50).
- Registered Self-Match Prevention (SMP) IDs in CME Firm Administrator Dashboard (FADB).

## Workflow

1. **Session Establishment**: Send Logon message (`35=A`) with outbound `MsgSeqNum=1`. Process `Logon` response and establish heartbeat timers (`35=0`).
2. **Order Construction**: Construct `NewOrderSingle` (`35=D`):
   - Populate `Tag 1028=N` for automated execution algorithms (`Y` if manual).
   - Populate `Tag 50` (Operator ID) per Rule 576.
   - Populate `Tag 7928` (SelfMatchPreventionID) and `Tag 8000` (SelfMatchPreventionInstruction, e.g. `O` = Cancel Outgoing, `R` = Cancel Resting).
3. **Sequence Management**: Increment outbound `MsgSeqNum` (Tag 34). If an inbound gap is detected (`InboundSeqNum > ExpectedSeqNum`), transmit `ResendRequest` (`35=2`).
4. **Execution Reporting**: Parse `ExecutionReport` (`35=8`) messages to update internal order states (`NEW`, `FILLED`, `PARTIALLY_FILLED`, `CANCELED`, `REJECTED`).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Omitting Tag 1028 (ManualOrderIndicator)**: CME Globex requires Tag 1028 on all orders. Submitting an order without `1028=N` or `1028=Y` results in a session-level reject (`35=3`).
- **Unregistered SMP IDs**: Passing an arbitrary string in Tag 7928 without prior registration in CME FADB. The order will be rejected.
- **Sequence Number Desynchronization**: Failing to handle `ResendRequest` (`35=2`) or `SequenceReset` (`35=4`), causing session disconnects during high-volatility bursts.

## Verification

- Instantiate `CmeFixSessionEngine`. Format an automated futures order (`35=D`). Verify that `Tag 1028=N`, `Tag 50`, and `Tag 7928/8000` are correctly serialized into the FIX string. Simulate a gap in inbound sequence numbers and verify that `ResendRequest` is emitted.
- Run `python scripts/test_cme_group_fix_api_for_futures.py`.

## Related Skills

- `cme-globex-futures-api-integration`
- `fix-protocol-session-management-across-venues`
