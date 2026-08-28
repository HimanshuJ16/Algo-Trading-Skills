# Standards for systemd Supervision of Trading Bots

Every directive this skill validates, with the source behind it. Where a
threshold or rule is a repository convention or an arithmetic derivation rather
than a published requirement, that is stated rather than implied.

All quotations are from the systemd manual pages and the systemd source tree
listed under *Sources*.

## Directive-to-source map

| Check (finding code) | Directive / source | What the source says |
|---|---|---|
| `START_LIMIT_IGNORED_IN_SERVICE_SECTION` | `systemd.unit(5)`, `[Unit]` options | `StartLimitIntervalSec=` / `StartLimitBurst=` are documented as `[Unit]` options. "Units which are started more than *burst* times within an *interval* time span are not permitted to start any more." |
| `START_LIMIT_IGNORED_IN_SERVICE_SECTION` | systemd source, `src/core/load-fragment-gperf.gperf.in` | The table carries `Unit.StartLimitIntervalSec`, `Unit.StartLimitInterval`, `Unit.StartLimitBurst`, `Unit.StartLimitAction`, then — under the comment "The following five only exist for compatibility, they moved into Unit, see above" — `Service.StartLimitInterval`, `Service.StartLimitBurst`, `Service.StartLimitAction`. **There is no `Service.StartLimitIntervalSec` entry**, so that spelling in `[Service]` is an unknown key. |
| `START_LIMIT_IGNORED_IN_SERVICE_SECTION` (consequence) | `systemd-system.conf(5)` | `DefaultStartLimitIntervalSec=` "defaults to 10s" and `DefaultStartLimitBurst=` to 5. A unit whose interval directive was ignored silently runs on the 10s window. |
| `START_LIMIT_LEGACY_SERVICE_SECTION` | same gperf table | `Service.StartLimitInterval` / `StartLimitBurst` / `StartLimitAction` are honoured, but only as compatibility aliases. |
| `MISSING_START_RATE_LIMIT` | `systemd.unit(5)` | Units reaching the limit "are not attempted to be restarted anymore"; `systemctl reset-failed` "will cause the restart rate counter for a service to be flushed". |
| `START_LIMIT_UNREACHABLE` | **Derived**, not published — see *Derived checks* below | — |
| `UNBOUNDED_RESTART_POLICY` | `systemd.service(5)`, `Restart=` | `always`: "the service will be restarted regardless of whether it exited cleanly or not". `on-failure`: restarts "when the process exits with a non-zero exit code, is terminated by a signal … when an operation (such as service reload) times out, and when the configured watchdog timeout is triggered." |
| `WATCHDOG_PINGS_IGNORED` | `systemd.service(5)`, `NotifyAccess=` | "Takes one of none (the default), main, exec or all. … If none, no daemon status updates are accepted from the service processes, all status update messages are ignored." |
| `WATCHDOG_PINGS_IGNORED` (why `Type=notify` rescues it) | `systemd.service(5)`, `Type=notify` | "If `NotifyAccess=` is missing or set to none, it will be forcibly set to main." |
| `MISSING_WATCHDOG` | `systemd.service(5)`, `WatchdogSec=` | "The service must call `sd_notify(3)` regularly with 'WATCHDOG=1' (i.e. the 'keep-alive ping'). If the time between two such calls is larger than the configured time, then the service is placed in a failed state and it will be terminated with SIGABRT (or the signal specified by `WatchdogSignal=`)." |
| `MISSING_MEMORY_LIMIT` | `systemd.resource-control(5)`, `MemoryMax=` | "If memory usage cannot be contained under the limit, out-of-memory killer is invoked inside the unit." And: "It is recommended to use `MemoryHigh=` as the main control mechanism and use `MemoryMax=` as the last line of defense." |
| `MISSING_STOP_TIMEOUT` | `systemd.service(5)`, `TimeoutStopSec=` | "it configures the time to wait for the service itself to stop. If it does not terminate in the specified time, it will be forcibly terminated by SIGKILL." |
| `MISSING_PREMARKET_HEALTHCHECK` | `systemd.service(5)`, `ExecStartPre=` | "If any of those commands (not prefixed with '-') fail, the rest are not executed and the unit is considered failed." |
| `MISSING_EXEC_START` / `MALFORMED_UNIT_FILE` | `systemd.unit(5)` syntax | Directives are section-scoped `Key=Value` assignments. |

## The sd_notify protocol

From `sd_notify(3)`:

- **Payload shape.** "The *state* parameter should contain a newline-separated
  list of variable assignments, similar in style to an environment block."
- **`READY=1`** — "Tells the service manager that service startup is finished,
  or the service finished re-loading its configuration."
- **`STATUS=…`** — "Passes a **single-line** UTF-8 status string back to the
  service manager that describes the service state." The single-line
  requirement is why `build_notify_message()` refuses a value containing a
  newline: the payload is newline-delimited, so an embedded newline does not
  corrupt the message, it *appends a further protocol field*.
- **`WATCHDOG=1`** — "Tells the service manager to update the watchdog
  timestamp. This is the keep-alive ping that services need to issue in regular
  intervals."
- **`STOPPING=1`** — "Tells the service manager that the service is beginning
  its shutdown."
- **`EXTEND_TIMEOUT_USEC=…`** — "Tells the service manager to extend the
  startup, runtime or shutdown service timeout." The extension applies "only if
  the runtime of the current state is beyond the original maximum times of
  `TimeoutStartSec=`, `RuntimeMaxSec=`, and `TimeoutStopSec=`".
- **Transport.** "These functions send a single datagram with the state string
  as payload to the socket referenced in the `$NOTIFY_SOCKET` environment
  variable. If the first character of `$NOTIFY_SOCKET` is '/' or '@', the string
  is understood as an AF_UNIX or Linux abstract namespace socket
  (respectively)."
- **Absence is not failure.** "If the `$NOTIFY_SOCKET` was not set and hence no
  status message could be sent, 0 is returned." This is why every notify call in
  `scripts/supervision_helper.py` returns `False` instead of raising.

## Watchdog cadence

From `sd_watchdog_enabled(3)`:

- `$WATCHDOG_USEC` is "set by the system manager for supervised process for
  which watchdog support is enabled, and contains the watchdog timeout in μs".
- `$WATCHDOG_PID` "contains the PID of that process". The watchdog counts as
  enabled only when `WATCHDOG_USEC` is set **and** `WATCHDOG_PID` is either
  unset or equal to the calling process's PID — which is what stops a forked
  child from inheriting the obligation.
- **Cadence:** "It is recommended that a daemon sends a keep-alive notification
  message to the service manager every half of the time returned here."

`watchdog_ping_interval_seconds()` implements exactly this, with the halving
exposed as `safety_factor` so a loop with a long tail latency can ping more
often. Nothing here hard-codes 15 seconds; that number is only correct for
`WatchdogSec=30`.

## Derived checks — arithmetic, not published rules

**`START_LIMIT_UNREACHABLE`** is a derivation from documented semantics, not a
systemd rule, and no regulator or vendor publishes a threshold for it.

The reasoning: systemd rate-limits *start attempts* within a sliding
`StartLimitIntervalSec` window. Automatic restarts are spaced by `RestartSec=`,
so `StartLimitBurst` consecutive attempts occupy at least
`RestartSec × (StartLimitBurst − 1)` seconds. When that span is not shorter than
the window, the burst count cannot be reached inside one window and the limiter
never trips:

```
RestartSec × (StartLimitBurst − 1)  >=  StartLimitIntervalSec   =>  unreachable
```

The engine reports this at `HIGH` rather than `CRITICAL` because a deliberately
unbounded restart policy is a legitimate (if unusual) choice — but it should be
a choice, and it is worth knowing that the pre-2.0.0 reference unit in this
skill satisfied the inequality (10 × 4 = 40s against a 10s effective window)
without anyone intending it.

The same finding code also covers the **documented** off-switch, which is not a
derivation: systemd.unit(5) states `StartLimitIntervalSec=` "may be set to 0 to
disable any kind of rate limiting", and the implementation generalises that to
either field — `src/basic/ratelimit.h` defines

```c
static inline bool ratelimit_configured(const RateLimit *rl) {
        return rl->interval > 0 && rl->burst > 0;
}
```

with `ratelimit_below()` returning "below the limit" unconditionally when the
limit is not configured. A zero in *either* directive therefore removes the
crash-loop brake entirely.

**`MemoryMax=` required.** A repository convention. systemd requires no memory
limit, and no regulator prescribes one; the argument is only that a tick-buffer
or order-book leak should take down one unit rather than the host. The size
(`1G` in the reference unit) is illustrative — derive yours from measured RSS
under peak subscription load, and note that both directives require cgroup v2.

**`TimeoutStopSec=` required to be explicit.** Also a convention. The manager
default is a real value, but how long a bot gets to cancel live orders before
SIGKILL should be a decision traceable to a measured unwind, not an inherited
default.

## Holiday handling — why `SuccessExitStatus=` is not the answer

A tempting pattern is to have `ExecStartPre=` exit with a distinguished status
on a market holiday and list that status in `SuccessExitStatus=`. The manual
does not support it: `SuccessExitStatus=` "Takes a list of exit status
definitions that, when returned by **the main service process**, will be
considered successful termination, in addition to the normal successful exit
status 0". `ExecStartPre=` runs as a control process, and the documentation
makes no guarantee for it.

The documented path is therefore: keep `ExecStartPre=` for faults only, and let
the **main process** decide about the calendar — send `READY=1`, observe that
the exchange is closed, exit 0. Under `Restart=on-failure` a clean exit is not
restarted, the unit goes `inactive` rather than `failed`, and no start-limit
slot is consumed. `HealthCheckResult.is_fault` exists to make that distinction
mechanical.

## Scope limits worth stating

- **This audits text, not a running manager.** Drop-ins under
  `/etc/systemd/system/<unit>.d/` override the file on disk. Audit
  `systemctl cat <unit>` output, and cross-check with
  `systemctl show -p StartLimitIntervalSec,StartLimitBurst <unit>`.
- **Directive availability is version-dependent.** `Type=notify-reload` arrived
  well after `Type=notify`; `MemoryMax=` needs cgroup v2. This engine does not
  know your systemd version. `systemd-analyze verify` does.
- **No regulatory claim is made here.** Process supervision is an operational
  control, not a prescribed one. Where a regime does speak to the resilience of
  automated trading systems — MiFID II RTS 6 on business continuity for
  algorithmic trading, or SEC Rule 15c3-5 on market-access risk controls — it
  addresses the *controls*, not the init system that hosts them. Do not cite a
  rule number for `WatchdogSec=`.

## Sources

- `sd_notify(3)` — https://man7.org/linux/man-pages/man3/sd_notify.3.html
- `sd_watchdog_enabled(3)` — https://man7.org/linux/man-pages/man3/sd_watchdog_enabled.3.html
- `systemd.service(5)` — https://man7.org/linux/man-pages/man5/systemd.service.5.html
- `systemd.unit(5)` — https://man7.org/linux/man-pages/man5/systemd.unit.5.html
- `systemd-system.conf(5)` — https://man7.org/linux/man-pages/man5/systemd-system.conf.5.html
- `systemd.resource-control(5)` — https://man7.org/linux/man-pages/man5/systemd.resource-control.5.html
- systemd source, `src/core/load-fragment-gperf.gperf.in` —
  https://github.com/systemd/systemd/blob/main/src/core/load-fragment-gperf.gperf.in
