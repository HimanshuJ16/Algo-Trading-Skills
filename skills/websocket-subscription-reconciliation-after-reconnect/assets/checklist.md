# Pre-Flight / Sign-off Checklist — websocket-subscription-reconciliation-after-reconnect

Use this before considering the skill's implementation complete.

## Subscription state

- [ ] **Decoupled desired state:** the desired-symbol set lives outside the connection
      object, is mutated only by strategy logic, and is the single source of every
      resubscription.
- [ ] **Concurrency:** desired state is guarded against simultaneous mutation from the
      SDK's network callback thread and the strategy thread.
- [ ] **Fresh resubscription:** reconnect issues one subscribe derived from the current
      set. No append-only subscribe log is replayed.
- [ ] **Single owner:** the SDK's own reconnect path has been read, and either the SDK or
      the application restores subscriptions — not both. (`KiteTicker` restores them
      itself.)
- [ ] **Symbols verbatim:** no silent case folding or rewriting of symbols; any
      normalisation is deliberate and matches the venue's convention.

## Connection lifecycle

- [ ] **Liveness detection:** heartbeats or Ping/Pong plus a staleness timer on a monotonic
      clock, so a half-open connection is detected rather than waited on forever.
- [ ] **Single disconnect record:** duplicate close/error callbacks for one drop do not
      reset the disconnect timestamp.
- [ ] **Old session torn down:** the previous socket is closed and the SDK object discarded
      before dialling, so a venue connection limit is not held by a zombie session.
- [ ] **Bounded jittered backoff:** exponential growth, capped, jittered, and clamped back
      inside the cap; the exponent is capped so a long outage cannot overflow it.
- [ ] **Auth before subscribe:** on streams with per-connection auth, re-authentication
      precedes resubscription.

## Coverage integrity

- [ ] **Acknowledgement reconciled:** the broker's confirmed subscription list is compared
      against desired state; missing and unexpected symbols are both logged at error level.
- [ ] **Quota checked:** the desired set fits the venue's symbol cap, connection limit and
      market-data entitlement, and a truncated resubscription is detected rather than
      assumed impossible.
- [ ] **Expiring subscriptions:** where the venue expires subscriptions independently of
      the connection (IBKR Client Portal `smd`, 10 minutes), a lifetime timer drives
      re-request as well as connection events.
- [ ] **Backfill ordering:** resubscription happens first, and the backfill window runs
      from disconnect through resubscription completion.
- [ ] **Backfill failure visible:** a failed backfill is surfaced as missing data, not
      logged and stepped over.
- [ ] **Monotonic gap measurement:** gap duration is monotonic-clock arithmetic; wall-clock
      stamps are recorded separately for the REST window and the audit record.
- [ ] **Bounded state:** reconnect history and deduplicator window are both bounded.
- [ ] **Deduplication key:** the downstream deduplicator keys on the feed's sequence number
      where one exists, not on `(symbol, timestamp)` alone.

## Evidence

- [ ] **Forced-disconnect drill:** a real mid-session disconnect restores exactly the
      pre-disconnect set, verified against the broker's acknowledgement.
- [ ] **Repeat-reconnect drill:** five or more disconnects in one session produce an
      identical subscribe payload each cycle.
- [ ] **Blackhole drill:** packets dropped without RST triggers the staleness timer.
- [ ] **Automated testing:** run
      `python -m unittest discover -s skills/websocket-subscription-reconciliation-after-reconnect/scripts`
      and confirm a 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
