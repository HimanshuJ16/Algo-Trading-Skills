# Standards — execution-algorithm-kill-switch-integration

## What is actually mandated, and by whom

Each row was checked against the primary source. Where no source mandates a
value, the row says so — do not cite this file as authority for a number it
calls a house default.

| Requirement | Source | What it actually says | Status |
|---|---|---|---|
| Immediate emergency cancellation of any or all unexecuted orders | Commission Delegated Regulation (EU) 2017/589 (MiFID II RTS 6) **Art. 12(1)** | An investment firm "shall be able to cancel immediately, as an emergency measure, any or all of its unexecuted orders submitted to any or all trading venues to which the investment firm is connected" ('kill functionality'). | Mandatory, EU |
| Kill functionality covers traders, desks and clients | RTS 6 **Art. 12(2)** | Unexecuted orders include those originating from individual traders, trading desks and, where applicable, clients. | Mandatory, EU |
| Per-order attribution to an algorithm and an owner | RTS 6 **Art. 12(3)** | The firm must be able to identify which trading algorithm and which trader, desk or client is responsible for each order sent to a trading venue. | Mandatory, EU |
| Kill functionality need **not** be one unified system | **ESMA Q&A on MiFID II/MiFIR market structures**, Q&A on kill functionality (ESMA70-872942901-38) | The obligation is the ability to immediately pull any or all outstanding orders from any or all venues. It does "not create an obligation for all systems connecting the firm to different trading venues to be implemented through a single unified piece of software"; the functionality may comprise procedures *and* switches. In any case a *single decision* must result in immediate withdrawal of all orders or any subset. | Regulator Q&A, EU |
| Pre-trade controls including maximum message limits | RTS 6 **Art. 15(1)** | Requires price collars, maximum order values, maximum order volumes and maximum message limits (preventing an excessive number of submission/modification/cancellation messages). **No numeric threshold is given.** | Mandatory, EU — value is the firm's |
| Continuous real-time monitoring, and post-trade action | RTS 6 **Art. 16**, **Art. 17** | Real-time monitoring of algorithmic trading; where a post-trade control triggers, the firm undertakes appropriate action which may include adjusting or shutting down the algorithm or an orderly withdrawal from the market. | Mandatory, EU |
| Documented risk management controls and supervisory procedures | **17 CFR 240.15c3-5(b)** | The broker-dealer "shall establish, document, and maintain a system of risk management controls and supervisory procedures reasonably designed to manage the financial, regulatory, and other risks" of market access. | Mandatory, US |
| Pre-set credit/capital thresholds; erroneous-order controls | **17 CFR 240.15c3-5(c)(1)(i)–(ii)** | Prevent entry of orders exceeding appropriate pre-set credit or capital thresholds; prevent erroneous orders by rejecting those breaching price or size parameters, order-by-order or over a short period of time. | Mandatory, US |
| Controls under the firm's direct and exclusive control | **17 CFR 240.15c3-5(d)** | The financial and regulatory risk controls must be under the "direct and exclusive control of the broker or dealer". | Mandatory, US |
| Annual review and CEO certification | **17 CFR 240.15c3-5(e)(1)–(2)** | Annual review of the controls' effectiveness, documented; annual CEO (or equivalent) certification. | Mandatory, US |

**Not found — do not claim it.** No regulator in the sources above publishes a
kill-switch *latency*. RTS 6 says "immediately"; 15c3-5 says "reasonably
designed" and never uses the phrase "kill switch". Any millisecond figure in a
runbook (this skill's 1.0.0 asserted "< 50 ms") is a firm engineering target and
must not be attributed to SEC or ESMA. Likewise no rule sets an order rate of
100 msgs/sec — RTS 6 Art. 15(1) mandates that a maximum message limit *exist*.

## FIX protocol facts used by the engine

| Element | Source | Detail |
|---|---|---|
| `OrderMassCancelRequest` MsgType `q`; `OrderMassCancelReport` MsgType `r` | FIX 4.4 specification | Required fields on the request: `ClOrdID` (11), `MassCancelRequestType` (530), `TransactTime` (60). |
| `MassCancelRequestType` (tag **530**), FIX 4.4 enumeration | FIX 4.4 | 1 = orders for a security · 2 = underlying security · 3 = Product · 4 = CFICode · 5 = SecurityType · 6 = trading session · **7 = all orders**. There is **no per-strategy or per-algorithm scope.** |
| `MassCancelResponse` (tag **531**) | FIX 4.4 | 0 = *Cancel Request Rejected* (see tag 532); 1–7 mirror the scope actioned. |
| `MassCancelRejectReason` (tag **532**) | FIX 4.4 | **0 = "Mass Cancel Not Supported"** · 1–6 = invalid/unknown security, underlying, product, CFICode, SecurityType, trading session · 99 = Other. A venue answering tag 532 has cancelled nothing. |
| Party-scoped mass cancel | FIX 5.0 SP2 `OrderMassCancelRequest` | Adds an optional `TargetParties` component (`NoTargetPartyIDs`, tag 1461) "to specify the parties to whom the Order Mass Cancel should apply", plus `MarketID` (1301) for type 8 and `MarketSegmentID` (1300) for type 9. Where the venue supports it, this — not tag 530=1 — is the correct fast path for a desk/strategy-scoped cancel. |
| `OrderMassActionRequest` MsgType `CA` | FIX 5.0 SP2 | `MassActionScope` (1374) extends scope to Market (8), Market Segment (9), Security Group (10) and issuer scopes. Modern venue APIs commonly use this rather than `q`. |

## Venue-side kill switches (the layer this engine cannot replace)

| Venue | Facility | Behaviour |
|---|---|---|
| Nasdaq (also BX, PSX) | Kill Switch — **Nasdaq Rule 6130**, BX Rule 4764, PSX Rule 3316 | Optional firm-set Net Notional Risk Exposure threshold. On breach, order-entry ports associated with the MPID are disabled and open orders are administratively cancelled. Re-enabling requires the participant to explain the trigger and ask Nasdaq operations to reactivate the MPID. |
| CME Globex | Kill Switch (Globex Credit Controls) | Risk administrators can immediately block all new order entry and cancel all working orders for a selected subset of, or all, the firm's SenderComp IDs. |
| NYSE (Pillar) | Kill Switch / risk controls | Cancels per the requested scope while **continuing to enforce existing cancel restrictions** — e.g. after the MOO/LOO cancellation cutoff, MOC/LOC orders cancel but MOO/LOO orders remain open. Direct evidence that "cancel all" does not mean every order dies. |

Cancel-on-disconnect (offered on Nasdaq SQF/OTTO/FIX among others) covers the
case this engine structurally cannot: your own process being the thing that
failed.

## Configuration defaults (calibrate before use)

None of these are regulatory constants.

| Parameter | Default | What it does |
|---|---|---|
| `max_daily_loss_usd` | none — required | Loss magnitude (positive) compared against `-daily_pnl_usd`. Derive from firm capital and the desk's stop policy. |
| `max_order_rate_per_sec` | none — required | Order-entry attempts per second treated as a runaway loop. 100/sec is a plausible starting point for one strategy on one venue and is badly wrong for a market maker. Calibrate against observed steady-state rate *and* your venue's message allowance. |
| `max_net_exposure_usd` | none — required | Compared against `abs(net_exposure_usd)`, so a runaway short breaches on the same limit as a runaway long. |
| `max_snapshot_age_seconds` | `5.0` | Rejects new orders when the risk snapshot is older than this. A house default. `None` disables the gate, which means trusting the caller never to pass stale risk data. |

## Known limitations

- **In-memory state.** The order map, kill latches and audit trail do not
  survive a restart — which silently drops the lockout *and* every pending
  cancel. Persist them or fail closed on start-up.
- **Cancels are requested, never proven.** The engine reports what the venue
  accepted. Only `apply_execution_report` moves an order to a terminal state,
  and a fill can still land in the race.
- **The local order book is a belief.** A missed `ExecutionReport` means live
  orders the engine cannot enumerate. This is why GLOBAL scope fans out to every
  configured gateway regardless of what the local map contains.
- **No position flattening.** Cancelling working orders leaves existing
  positions untouched.
- **No authorisation model.** `triggered_by` and `authorized_by` are recorded,
  not verified — see `emergency-manual-override-access-control`.
- **PnL, exposure and (optionally) order rate are supplied by the caller.** The
  engine can only measure the order attempts that pass through it, which is a
  subset of the message traffic RTS 6 Art. 15(1) counts.
