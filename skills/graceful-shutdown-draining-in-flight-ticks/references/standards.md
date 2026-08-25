# Real-Time Architecture Standards — graceful-shutdown-draining-in-flight-ticks

## Supervisor Termination Budgets

The supervisor, not the process, decides when the drain is cut short. Size
$T_{\text{max\_drain}}$ below the applicable grace period.

| Supervisor | First signal | Default grace period | Escalation | Configured by |
|---|---|---|---|---|
| Kubernetes | `SIGTERM` to PID 1 of each container | 30 s | `SIGKILL` at expiry | `terminationGracePeriodSeconds` |
| Docker (`docker stop`) | `SIGTERM` | 10 s (Linux containers; 30 s Windows) | `SIGKILL` after grace period | `--timeout` |
| systemd | `KillSignal=`, default `SIGTERM` | `DefaultTimeoutStopSec=`, default 90 s | `SIGKILL` unless `SendSIGKILL=no` | `TimeoutStopSec=` |

Kubernetes specifics that change the arithmetic:

- The grace-period countdown begins **before** the `preStop` hook runs, and the
  hook must complete before `SIGTERM` is delivered. preStop time and drain time
  share one budget.
- EndpointSlice removal happens **at the same time** as the kubelet starts
  graceful shutdown — not before it. New ticks can still arrive after `SIGTERM`
  until removal propagates, which is what a `preStop` sleep covers.

## Shutdown Policy Matrix

| Shutdown Trigger | Ingress Policy | Drain Deadline | Offsets | Exit Code |
|---|---|---|---|---|
| `SIGTERM` (K8s/Docker/systemd) | Reject new ticks | `resolve_drain_timeout(grace)` | Commit after full flush | `0` |
| `SIGINT` (Ctrl+C) | Reject new ticks | `resolve_drain_timeout(grace)` | Commit after full flush | `0` |
| Second `SIGINT`/`SIGTERM` | Reject new ticks | Abandon drain immediately | Do **not** commit | `1` |
| Drain deadline breached | Reject new ticks | Stop drain, retain queued items | Do **not** commit | `1` |
| Sink flush failing at deadline | Reject new ticks | Retry until deadline | Do **not** commit | `1` |

Exit codes are defined in `scripts/graceful_shutdown.py` as `EXIT_CLEAN = 0` and
`EXIT_INCOMPLETE_DRAIN = 1`. A non-zero exit is the only durable signal that a
deploy lost in-flight data; do not mask it.

## Offset Commit Ordering

Kafka's own guidance for coupling consumption to downstream processing: commit
offsets only after the corresponding records have been written to the sink. Used
this way Kafka provides **at-least-once** delivery — a record may be duplicated
on restart but is not lost. Committing first yields at-most-once, where a failure
between commit and flush silently drops the records. For a tick sink this makes
duplicate-tolerant sink writes (idempotent upsert on `(symbol, timestamp)`) the
correct design, not commit-first.

`KafkaConsumer` is not thread-safe; `wakeup()` is the only method safe to call
from another thread, so a signal handler must not touch the consumer directly.

## Python Signal Semantics

- "Python signal handlers are always executed in the main Python thread of the
  main interpreter, even if the signal was received in another thread."
- `signal.signal()` "can only be called from the main thread of the main
  interpreter; attempting to call it from other threads will cause a `ValueError`
  exception to be raised."
- Handlers do not run inside the C-level handler; the flag is checked at the next
  bytecode. "A long-running calculation implemented purely in C ... may run
  uninterrupted for an arbitrary amount of time, regardless of any signals
  received."

Consequence: the handler sets flags only, and the drain runs on a thread that
reaches a bytecode boundary promptly.

## Regulatory Note — EU/EEA only

For investment firms engaged in algorithmic trading under MiFID II, Commission
Delegated Regulation (EU) 2017/589 (RTS 6) requires business continuity
arrangements that include "arrangements for shutting down the relevant trading
algorithm or trading system where appropriate" (Article 14(2)(f)), and that the
firm "shall ensure that its trading algorithm or trading system can be shut down
in accordance with its business continuity arrangements without creating
disorderly trading conditions" (Article 14(3)). Article 14(4) requires annual
review and testing of those arrangements.

Scope note: this is an EU/EEA obligation on in-scope investment firms. It is not
a global requirement, and it governs orderly shutdown of *trading* systems — a
market-data drain supports it but does not by itself discharge it. No equivalent
prescriptive drain requirement is asserted here for other jurisdictions.

## Sources

- Kubernetes — Pod Lifecycle, Termination of Pods:
  https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/
- Kubernetes — Container Lifecycle Hooks (preStop and grace period interaction):
  https://kubernetes.io/docs/concepts/containers/container-lifecycle-hooks/
- Docker — `docker container stop` CLI reference (default timeout):
  https://docs.docker.com/reference/cli/docker/container/stop/
- systemd — `systemd.kill(5)` (`KillSignal=`, `SendSIGKILL=`) and
  `systemd-system.conf(5)` (`DefaultTimeoutStopSec=`):
  https://www.freedesktop.org/software/systemd/man/latest/systemd.kill.html
- Python — `signal` module documentation (handler threading, `ValueError`):
  https://docs.python.org/3/library/signal.html
- Apache Kafka — `KafkaConsumer` javadoc, "Manual Offset Control" and thread
  safety: https://kafka.apache.org/documentation/
- Commission Delegated Regulation (EU) 2017/589 (RTS 6), Article 14:
  https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0589

## Category

`real-time-architecture` — see top-level `mappings/` directory.
