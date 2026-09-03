# UK Algorithmic Trading Order-Entry Controls — Sign-Off Checklist

Scope: MiFID RTS 6 Art. 15 order-entry controls and Art. 12 kill functionality, under
FCA Handbook MAR 7A.3.2R. **RTS 6 prescribes no numeric limit** — every value below
is the firm's own calibration under Art. 15(4), and each needs a recorded basis.

## Pre-trade controls (RTS 6 Art. 15(1))
- [ ] **Price collar (Art. 15(1)(a))** — collar percentage set per instrument tier, with
      the calibration basis recorded (volatility, tick size, venue price bands).
- [ ] **Reference price documented** — which price the collar measures against
      (primary-venue BBO mid, last trade, internal mark). There is no UK NBBO.
- [ ] **Missing reference fails closed** — a zero, negative or NaN reference rejects the
      order; it must never skip the collar.
- [ ] **Periodic dimension addressed** — Art. 15(1)(a) requires discrimination
      order-by-order **and** "over a specified period of time". If this engine covers only
      the per-order dimension, name the control that covers the other.
- [ ] **Maximum order value (Art. 15(1)(b))** — cap justified against the firm's own order
      distribution, not a round number.
- [ ] **Maximum order volume (Art. 15(1)(c))** — cap justified against ADV / liquidity tier.
- [ ] **Maximum messages limit (Art. 15(1)(d))** — `max_msg_rate_per_sec` set to the lower
      of the venue-facing message limit and the Art. 10 stress-tested throughput.
- [ ] **Unusable capacity state fails closed** — a zero or missing ceiling rejects rather
      than reporting 0% utilisation.
- [ ] **Erroneous-order validation (MAR 7A.3.2R(3))** — NaN, Inf, zero and negative price
      or quantity, and unknown order sides, are rejected before any threshold comparison.
- [ ] **Hard blocks, not soft alerts** — no control in the order path can be dismissed by
      the trader. (CGML, FCA final notice 22 May 2024, £27,766,200.)

## Immediacy, throttles and limits (Art. 15(2)-(5))
- [ ] **Counters updated immediately (Art. 15(2))** — every order sent to a venue is in the
      pre-trade limit calculation before the next evaluation.
- [ ] **Repeated-execution throttle configured (Art. 15(3))** — `max_repeated_executions`
      and the window are set; leaving the count `None` means the control does not exist.
- [ ] **Auto-disable requires manual re-enable (Art. 15(3))** — a tripped throttle latches
      the kill switch and is cleared only by a designated staff member.
- [ ] **Credit and market risk limits (Art. 15(4))** — derived from the capital base and
      clearing arrangements, reviewed when either changes, and adjusted for changing price
      and liquidity levels.
- [ ] **Limits live outside the strategy** — no risk limit travels on the order object;
      a strategy cannot widen the ceiling it is checked against (Art. 1(c) separation).
- [ ] **Permission and threshold blocking (Art. 15(5))** — orders from a trader without
      permission for an instrument, or that compromise firm risk thresholds, are blocked.

## Override procedure (Art. 15(6))
- [ ] **Override path documented and outside the gate** — specific trade, temporary basis,
      exceptional circumstances only.
- [ ] **Risk management function verifies** each override.
- [ ] **A named individual authorises** each override, and the record survives.

## Kill functionality (RTS 6 Art. 12)
- [ ] **Mass-cancel path implemented** — a real venue cancel interface is wired to
      `mass_cancel_handler`; no canned cancellation counts are reported.
- [ ] **Cancel path tested against the venue's test environment**, and separately against a
      forced failure.
- [ ] **Latch precedes cancel** — a failing mass cancel leaves the halt in force and the
      failure recorded, never unlatched.
- [ ] **Scope is explicit** — firm-wide only via `algo_id=None`; blank identifiers raise.
- [ ] **Per-algo reset does not lift a firm-wide halt**, and this is understood by the
      on-call team.
- [ ] **Attribution (Art. 12(3))** — the firm can identify which algorithm, trader, desk or
      client is responsible for each order sent to a venue.
- [ ] **Compliance access (Art. 2(2))** — compliance staff have contact with, or direct
      access to, the kill functionality at all times.
- [ ] **Reset requires a named authoriser and a reason**, both recorded.

## Records and governance
- [ ] **Every decision persisted** — `ControlCheckResult` for rejections and throttles, and
      `kill_switch_events`, written to durable storage. The engine holds them in memory only.
- [ ] **Timestamps timezone-aware**, and clocks synchronised per the firm's clock-sync
      obligations.
- [ ] **Retention** — at least five years for MiFID business records (SYSC 9.1); the
      Art. 28(3) five-year Annex II order-record rule applies only if the firm uses a
      high-frequency algorithmic trading technique.
- [ ] **Annual self-assessment (Art. 9)** — validation report drawn up by the risk
      management function, audited by internal audit where one exists, approved by senior
      management. RTS 6 names no SM&CR function; record who signs under the firm's own
      allocation of responsibilities.
- [ ] **Calibration review scheduled**, and material changes routed through Art. 11 change
      management.
- [ ] **Out-of-scope RTS 6 controls owned elsewhere** — Arts. 5–11, 13, 14, 16, 17, 18 each
      have a named owner; this order-entry gate is not the firm's whole RTS 6 programme.
