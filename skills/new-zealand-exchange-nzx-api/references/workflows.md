# Workflows for New Zealand Exchange (NZX) Order Entry

## 1. Provision the FIX session identity

Obtain `BeginString`, `SenderCompID`, `TargetCompID` and any `TargetSubID` from
the order-entry FIX specification NZX issued to your firm. `NZXFixSessionConfig`
has no defaults on purpose — a guessed `TargetCompID` fails at Logon in the best
case and routes somewhere unintended in the worst.

```python
session = NZXFixSessionConfig(
    sender_comp_id="<from NZX spec>",
    target_comp_id="<from NZX spec>",
    begin_string="<from NZX spec>",
)
engine = NewZealandExchangeNZXEngine(session, seq_num_provider=fix_session.next_out_seq)
```

Session management — Logon/Logout, heartbeating, sequence-number persistence,
ResendRequest handling — belongs to your FIX engine, not to this module. This
module only builds and parses application messages, and takes `MsgSeqNum (34)`
from you.

## 2. Classify the instrument before pricing it

Set `NZXOrderRequest.security_type`:

- `FUND` — every NZX listed fund, which ticks at $0.001 at any price;
- `EQUITY` — everything else, which uses the price-dependent band schedule;
- `DEBT_YIELD_QUOTED` — NZDX securities quoted in yield, which this engine
  rejects rather than mis-validating. NZDX hybrids quoted per $100 are `EQUITY`.

The default is `EQUITY` because that is the fail-closed direction: a fund misread
as an equity has its valid $0.001 price *rejected*, whereas an equity misread as
a fund would have a sub-tick price *accepted and sent*.

## 3. Price-step compliance audit

`validate_price_tick(price, security_type)` returns `(is_valid, step)` using
exact `Decimal` arithmetic — no float modulo, no tolerance window, so a price a
thousandth below a tick boundary cannot round its way into a valid order.
Non-positive prices are never valid.

Because Participant Rule 11.9.1 lets NZX respecify the steps at any time, treat
`NZXTickSchedule` as configuration:

```python
engine = NewZealandExchangeNZXEngine(session, tick_schedule=NZXTickSchedule(...))
```

Reconcile the configured schedule against the current NZX notice on a defined
cadence; do not assume the shipped default stays correct indefinitely.

## 4. Gate on the session phase

`NZXSessionSchedule.phase_at(dt)` maps an Auckland wall-clock instant to a phase.
Convert any UTC instant to Auckland time once, at the boundary of your scheduler,
and never inside trading logic — daylight saving shifts the UTC offset
(NZST UTC+12 → NZDT UTC+13), not the local session times.

- `is_order_entry_window(dt)` — Pre-Open, Normal Trading, Pre-Close.
- `is_cancel_window(dt)` — the above plus Adjust, where orders may be amended
  and withdrawn but not entered.

Pass `at_time=` to `build_fix_new_order_single` to have the build refuse an
out-of-phase order.

Two cautions. The opening and closing auctions fire at a random instant within
±30 seconds of 10:00 and 17:00, so this helper cannot be used to race the
auction — wait for the exchange session-state message. And the helper is
time-of-day only: it will report NORMAL on a NZ public holiday, so gate on a
trading calendar as well.

## 5. Build the NewOrderSingle (35=D)

```python
report = engine.build_fix_new_order_single(order, seq_num=next_seq, at_time=now_nz)
if report.status != "NEW":
    handle_local_reject(report.rejection_reason)   # nothing was sent
else:
    fix_session.send(report.fix_raw_payload)
```

Behaviour that matters:

- Every field is validated strictly. An unrecognised `side`, `order_type`,
  `time_in_force`, `security_type` or `quantity` produces a `REJECTED` report
  naming the field. Nothing is coerced — a coerced field means sending a real
  order that differs from the one requested, and a limit-to-market coercion
  would also skip price validation entirely.
- `Price (44)` is emitted for LIMIT orders only, rendered at the precision of the
  step it was validated against (`30.00`, not an ambiguous `30`). A MARKET order
  omits the tag; a price supplied on a market order is logged and ignored.
- `ClOrdID` and `Symbol` are checked for SOH, `=`, `|` and non-ASCII characters.
  An identifier taken from an upstream system and pasted into a tag=value string
  is a field-injection vector.
- Vendor symbology (`FPH.NZ`) is refused; `Symbol (55)` carries the bare ticker.
- The message is fully framed: `BeginString (8)`, `BodyLength (9)`,
  `MsgSeqNum (34)`, `SendingTime (52)`, `CheckSum (10)`, SOH-delimited.
  `field_delimiter="|"` produces a readable message for logs and tests, with
  BodyLength and CheckSum still computed over the emitted bytes — never send it.
- A local reject carries `fix_msg_type == ""` and an empty payload, because no
  FIX message was built and the exchange never saw the order. It is not an
  ExecutionReport.

## 6. Cancel an order (35=F)

```python
cancel = engine.build_fix_order_cancel_request(
    orig_cl_ord_id=working_id, cl_ord_id=new_id, symbol="FPH", side="BUY", seq_num=n)
```

`ClOrdID (11)` must be a NEW identifier distinct from `OrigClOrdID (41)`; reusing
the original is a FIX protocol error and is refused here.

The returned status is `PENDING_CANCEL`, not `CANCELED`. A cancel request is a
request: the order stays live on the book and can still fill until an
ExecutionReport confirms `OrdStatus=4`. Keep applying fills to the order until
that confirmation arrives — a strategy that decrements its position on the
*request* will run a phantom short.

## 7. Process ExecutionReports (35=8)

```python
exec_rpt = engine.parse_execution_report(raw)
```

The parser verifies `CheckSum (10)` and refuses a corrupt or truncated message;
refuses any MsgType other than `8` (a session Reject 35=3 or an
OrderCancelReject 35=9 is not an ExecutionReport and confirms nothing about the
order); requires `ClOrdID (11)`, `ExecID (17)` and `OrdStatus (39)`; and surfaces
an unrecognised `OrdStatus` as `UNKNOWN` rather than guessing.

Drive position state from `cum_qty` (14) and `leaves_qty` (151), never by
accumulating `last_qty` (32) yourself. ExecutionReports can be replayed after a
reconnect with `PossDupFlag (43)=Y` — `poss_dup` is exposed for exactly this —
and summing per-fill quantities across a duplicate double-counts the position.

This is a flat top-level decoder: it keeps the first occurrence of each tag and
does not descend into repeating groups. A message carrying groups that reuse
these tags needs a full FIX engine.

## 8. Audit report

Every build returns an `NZXOrderReport` carrying `status`, `fix_msg_type`,
`fix_raw_payload`, `audit_notes`, and — on a reject — `rejection_reason`,
`normalized_price` and `tick_size`. Persist these alongside the exchange's
ExecutionReports so a post-trade review can distinguish an order the exchange
rejected from one this engine refused to send.
