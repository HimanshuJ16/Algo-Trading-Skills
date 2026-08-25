# Pre-Flight / Sign-off Checklist — graceful-shutdown-draining-in-flight-ticks

Use this before considering the skill's implementation complete.

## Budget

- [ ] **Grace Period Known:** Supervisor budget recorded (K8s `terminationGracePeriodSeconds`, `docker stop --timeout`, systemd `TimeoutStopSec=`).
- [ ] **Drain Budget Derived:** $T_{\text{max\_drain}}$ computed from that grace period via `resolve_drain_timeout()`, not chosen arbitrarily.
- [ ] **preStop Accounted:** On Kubernetes, `preStop` duration subtracted from the same budget as the drain.

## Signals

- [ ] **Signal Registration:** `SIGTERM` and `SIGINT` traps registered on the main thread of the main interpreter.
- [ ] **Registration Result Checked:** A `False` return from `register_signal_handlers()` fails the deployment rather than being logged and ignored.
- [ ] **Handler Is Non-Blocking:** Handler sets flags only — no sink writes, no offset commits, no blocking I/O.
- [ ] **Escalation Path:** A second signal abandons the drain for an operator watching a wedged shutdown.

## Drain

- [ ] **Ingress Block:** External ingestion stops on signal receipt via `is_accepting_ingress()`.
- [ ] **Post-SIGTERM Traffic Handled:** `preStop` sleep configured, since EndpointSlice removal is concurrent with `SIGTERM`.
- [ ] **Monotonic Deadline:** Drain timing uses `time.monotonic()`, never `time.time()`.
- [ ] **Ack-Then-Remove:** Items leave the queue only after the sink accepts them; a failed flush retries instead of discarding.
- [ ] **Concurrency:** Producer threads share a lock with the drain, or the queue is provably single-writer at shutdown.

## Durability

- [ ] **Flush Before Commit:** Offsets committed only after a complete, successful sink flush.
- [ ] **No Commit After Loss:** An incomplete drain skips the offset commit so a restart replays.
- [ ] **Duplicate Tolerance:** Sink writes are idempotent, since flush-then-commit is at-least-once.
- [ ] **Deterministic Exit:** `exit_code` 0 only on a clean drain; 1 surfaced to the orchestrator on any loss.
- [ ] **Forensics:** Drained/undrained counts, flush failures and commit status logged.

## Testing

- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/graceful-shutdown-draining-in-flight-ticks/scripts` — 100% pass rate.
- [ ] **Failure Drills:** Sink-always-fails, sink-flaps, deadline-breach, double-signal and off-main-thread cases each rehearsed.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
