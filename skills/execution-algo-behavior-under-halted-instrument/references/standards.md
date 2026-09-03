# Standards — execution-algo-behavior-under-halted-instrument

> **Jurisdiction and scope.** Limit Up-Limit Down (LULD) is a **US NMS equities**
> National Market System plan. CME Globex, Eurex T7, JPX, ASX and NSE/BSE use
> materially different phase models, different price-band regimes and — critically
> for this skill — **different rules about when a cancel will be accepted**. Do not
> port the state tokens or the cancel assumptions to another venue without
> re-deriving them from that venue's rulebook and technical specification.

## 1. What actually happens to a resting order when trading stops

| Venue / phase | Order entry | Modify | Cancel | Matching |
|---|---|---|---|---|
| CME Globex — `Pause` | ✗ | ✗ | **✓** | ✗ |
| CME Globex — `Pre-Open` | ✓ | ✓ | ✓ | ✗ |
| CME Globex — `Pre-Open - No Cancel` | ✓ | ✗ | **✗** | ✗ |
| CME Globex — `Close - Final` | ✗ | ✗ | **✗** | ✗ |
| Eurex T7 — volatility interruption auction | ✓ | ✓ | ✓ | ✗ |
| Eurex T7 — extended VI **freeze phase** | ✗ | ✗ | **deletion held as *pending*** | ✗ |
| US equities — LULD trading pause | see below | see below | ✓ | ✗ |

Three consequences drive the engine's design:

- **`Pre-Open - No Cancel` and the extended-VI freeze are the dangerous phases.** They
  sit immediately before an auction match, which is precisely when a stale resting
  order is most expensive — and they are the phases in which you cannot pull it.
  An engine that assumes "halt ⇒ I can cancel" is wrong exactly when it matters.
- **A cancel is a request.** Nothing above guarantees the cancel is actioned, and
  none of it prevents the order from filling first. Track
  `RESTING → PENDING_CANCEL → CANCELLED/FILLED` against venue acknowledgements.
- **A cancel reject leaves the order live.** It does not retire it.

### US equities specifics

- Resting orders **persist through a LULD pause** and are eligible interest for the
  reopening cross; they do not evaporate because trading stopped.
- Cboe cancels or preserves resting orders **based on a configurable port-level
  setting** — so the behaviour of your own book depends on how the session was
  provisioned. Verify it per port rather than assuming.
- **Market orders are rejected while the security is in a Limit State or a Straddle
  State.** This is the reason `marketable_child_orders_permitted` exists separately
  from `is_slicing_active`: the instrument is still trading, but aggressive children
  will bounce.
- If a LULD pause runs into the close and no halt cross can occur before 16:00,
  Nasdaq cancels DAY / LOC / MOC / IO orders back to the entering firm — the parent
  ends the session short of target with its orders killed by the venue.

## 2. Halt geometry (US NMS equities)

| Mechanism | Definition |
|---|---|
| Limit State | NBO equals the Lower Price Band, or NBB equals the Upper Price Band |
| Straddle State | NBB is **below** the Lower Price Band, or NBO is **above** the Upper Price Band |
| Escalation | A Limit State persisting **15 seconds** triggers a **5-minute** Trading Pause |
| Pause extension | The LULD Plan permits extension to **10 minutes** |
| Reopening | Reopening auction at the **primary listing exchange**; new bands are derived from the auction price |

In a Straddle State the primary listing exchange *may* declare a pause if trading
deviates from normal characteristics — it is discretionary, not automatic, so treat
a straddle as a warning rather than a scheduled event.

**Derivatives contrast.** A CME Velocity Logic event suspends matching for roughly
**5–10 seconds** and moves the instrument to `Pre-Open` or `Reserved`, not to a
five-minute pause; resting mass quotes are cancelled by the auto-reserve function.
An outage that short does not warrant re-benchmarking a multi-hour schedule. Note
that CME documents order permissions for the `Pause` *state* (cancel only) and for
the `Pre-Open`/`Reserved` states a Velocity Logic event transitions into (entry,
modify and cancel permitted, **no market orders**) separately — check which state
your instrument actually enters rather than assuming "Velocity Logic ⇒ Pause".

## 3. Regulatory context — what is and is not mandated

| Claim | Status |
|---|---|
| An investment firm must be able to cancel immediately, as an emergency measure, any or all unexecuted orders across venues ("kill functionality") | **Mandatory** for EU algorithmic trading firms — MiFID II RTS 6 (Commission Delegated Regulation (EU) 2017/589), Article 12 |
| A firm must be able to identify which algorithm and which trader/desk/client is responsible for each order sent to a venue | **Mandatory** — RTS 6, Article 12 |
| An execution algo must cancel resting orders within a specific number of milliseconds of a halt | **Not a published standard.** No regulator or exchange publishes a halt-reaction latency SLA. Any such number is a firm-internal target |
| An execution algo must cap its post-halt catch-up rate | **Not a published standard.** A firm-calibrated impact control |

RTS 6 Article 12 mandates the *capability* to cancel, not a cancel-on-halt policy
and not a latency figure. Cancelling resting children on a halt is sound execution
practice justified by the auction-eligibility mechanics in §1 — present it as that,
not as a regulatory requirement.

## 4. Engine parameters (calibrate; these are not standards)

| Parameter | Default | What it does |
|---|---|---|
| `max_rate_multiple` | `1.5` | Post-halt required rate may exceed the original scheduled rate by at most this multiple before the engine refuses to resume slicing. |
| `min_remaining_seconds` | `1.0` | Below this remaining horizon the residual cannot be expressed as a schedule; the engine escalates rather than computing an unbounded rate. |
| `hard_end_ts` | caller-supplied | Absolute deadline (typically session close) past which the schedule is never extended. |

Calibrate `max_rate_multiple` against your own market-impact model and the
instrument's liquidity tier, and record the rationale. A value of `1.0` forbids any
catch-up at all; large values re-create the backlog-dumping behaviour the guard
exists to prevent.

## Sources

- Cboe, *Limit Up/Down FAQ* — Limit State and Straddle State definitions, 15-second escalation to a 5-minute pause, market orders rejected in Limit/Straddle state, resting orders cancelled-or-preserved by port-level setting: https://www.cboe.com/document/tech-spec/document/technical-specifications/cboe-limit-updown-faq
- CME Group Client Systems Wiki, *Market and Instrument States* — per-state order entry / modify / cancel / matching permissions, including `Pause` and `Pre-Open - No Cancel`: https://cmegroupclientsite.atlassian.net/wiki/display/EPICSANDBOX/Market+and+Instrument+States
- CME Group Client Systems Wiki, *Velocity Logic* — 5–10 second matching suspension, transition to Pre-Open/Reserved, mass-quote cancellation on auto-reserve: https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457218368/Velocity+Logic
- Eurex, *Volatility Interruption Functionality* — order maintenance during the VI auction; deletions held as pending during the extended-VI freeze phase: https://www.eurex.com/ex-en/support/emergencies-and-safeguards/volatility-interruption-functionality
- LULD Plan (National Market System Plan to Address Extraordinary Market Volatility) — Section VII trading pauses, 5-minute pause extendable to 10, reopening at the primary listing exchange: https://www.luldplan.com/
- Nasdaq, *LULD hybrid halt process / closing cross* — resting orders remain eligible interest for the cross; DAY/LOC/MOC/IO cancelled back when no halt cross can occur before 16:00: https://www.nasdaqtrader.com/content/NewsAlerts/2020/ETA/LULD_hybrid_halt_process_ETC_v.3.pdf
- MiFID II RTS 6, Commission Delegated Regulation (EU) 2017/589, Article 12 (Kill functionality): https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng
