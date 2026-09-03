# CME FIX Pre-Flight Checklist

## Protocol
- [ ] Is the destination confirmed to accept **tag=value FIX**? CME order entry does not —
      iLink 2 was decommissioned on MSGW 2021-03-28 and on CGW 2025-04-06.
- [ ] Is `BeginString` right for the surface (`FIX.4.4` for CME STP)?
- [ ] Is the wire delimiter SOH (`0x01`), with `|` used only in logs?

## Framing
- [ ] Are `8`, `9`, `35` the first three fields and `10` the last?
- [ ] Does `BodyLength(9)` count the bytes from the end of the `9=` field to the start of
      `10=`, inclusive of the preceding delimiter?
- [ ] Is `CheckSum(10)` the sum of all preceding bytes mod 256, zero-padded to 3 digits?
- [ ] Is `SendingTime(52)` UTC, at the precision the venue expects?
- [ ] Is the built message transmitted **verbatim**, with no downstream re-encoding?
- [ ] Is any field value carrying SOH or `=` rejected before serialisation?

## Order tags
- [ ] Is `1028` explicitly `N` for automated flow and `Y` for human-entered orders?
- [ ] Does the reject handler treat a Business Reject (`35=j`, `380=100`) as a rejection?
      A missing `1028` arrives that way, not as `35=3`.
- [ ] Is `50` the operator ID registered under CME Rule 576?
- [ ] Is `7928` an SMP ID registered in FADB — and numeric, if this code may ever move to
      iLink 3 (`2362` is a `uInt64`)?
- [ ] Is `8000` restricted to `O` (cancel oldest/resting) or `N` (cancel newest/aggressing),
      with anything else rejected before it reaches the wire?
- [ ] Is Tag 1 (Account) explicitly supplied, with no placeholder default anywhere in the
      call path?
- [ ] Are prices decimal strings or `Decimal`, serialised unrounded and without scientific
      notation?

## Sequencing
- [ ] Are both sequence counters persisted and restored on reconnect?
- [ ] Does a detected gap emit exactly **one** `ResendRequest`, with no re-request while
      that range is outstanding?
- [ ] Does recovery state clear once the requested range has been filled, so a later gap is
      still detected?
- [ ] Is `SequenceReset(35=4)` with `GapFillFlag≠Y` honoured regardless of its own `34`,
      and rejected if `NewSeqNo` moves backwards?
- [ ] Does a too-low `MsgSeqNum` without `PossDupFlag=Y` send `Logout(35=5)` **and** drop
      the connection, rather than being logged and ignored?
- [ ] Is business processing gated on "this message was in sequence", rather than on the
      absence of a response to send? Duplicates and mid-recovery messages must not be
      applied to order state.
- [ ] Is a discarded/unsent built message accounted for? The counter advances at build
      time, so an unsent message leaves a hole the peer will ask you to fill.

## Before production
- [ ] Has a built message been sent through the real transport on a venue test session and
      been accepted? Unit tests cannot see a transport that rewrites bytes.
- [ ] Is heartbeat, `TestRequest` and resend replay handled by a real session engine, since
      this module does none of it?
