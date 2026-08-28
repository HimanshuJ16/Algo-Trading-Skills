---
name: systemd-supervision-for-trading-bots
description: >-
  Use when a trading bot runs as a systemd service on a Linux host and you need
  its supervision to be correct rather than merely present — auditing a .service
  unit for directives written in the wrong section, a watchdog whose pings
  systemd silently discards, and a start rate limit that can never trip; plus
  sd_notify READY=1 / WATCHDOG=1 / STOPPING=1 / EXTEND_TIMEOUT_USEC handling and
  an ExecStartPre pre-market gate that tells a fault apart from a market holiday.
domain: algorithmic-trading
subdomain: deployment-ops
tags:
- deployment-ops
- systemd
- process-supervision
- sd-notify
- watchdog
- graceful-shutdown
- pre-market-healthcheck
brokers_frameworks:
- systemd (Linux service manager)
- sd_notify(3) service notification protocol
- systemd.service(5) / systemd.unit(5) directives
- systemd.resource-control(5) cgroup v2 limits
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when a bot that can send orders runs as a systemd unit on a
Linux host, and you need to answer three questions that a unit file cannot
answer by looking healthy:

1. **Will the crash-loop brake actually engage?** Not "is `StartLimitBurst=`
   present" — will systemd reach it before the bot has hammered a degraded
   broker for an hour.
2. **Is the watchdog watching anything?** A `WatchdogSec=` that systemd honours
   while every `WATCHDOG=1` is discarded is worse than no watchdog: it kills a
   healthy bot on a timer. A ping sent from a thread that survives the loop
   wedging is worse again: it reports health that does not exist.
3. **What happens between SIGTERM and SIGKILL?** That interval is where open
   orders are either cancelled or abandoned at the broker.

The engine here is not a wrapper around `systemctl`. It sends the sd_notify
datagrams a supervised bot owes systemd, and it audits unit-file *text* — which
is the part where the expensive mistakes are silent.

## When NOT to Use

- **You want to know what systemd actually loaded.** This audits the text you
  hand it. Drop-ins under `/etc/systemd/system/<unit>.d/` override the file on
  disk and are invisible here, as are directives your systemd build is too old
  to recognise. Feed it `systemctl cat <unit>` output, and treat
  `systemd-analyze verify` as the complementary check that the running manager
  agrees.
- **You are not on systemd.** Docker/Kubernetes restart policies, supervisord,
  runit and launchd share none of this vocabulary. The *ideas* transfer — a
  liveness probe that proves the trading loop is alive, a bounded restart
  budget, a drain window before SIGKILL — but nothing in `scripts/` applies.
- **You want supervision to be your risk control.** A watchdog restart is a
  blunt instrument: it terminates the process, it does not flatten a position or
  cancel resting orders. Order-level protection belongs to
  `kill-switch-and-drawdown-circuit-breakers` and
  `execution-algorithm-kill-switch-integration`; this skill keeps the process
  that runs them alive, and gets out of the way cleanly when it should not be.
- **Your bot is stateless and idempotent on restart.** Then most of this is
  ceremony. The care here is warranted because a restarted trading bot wakes up
  with positions it did not open and orders it does not remember placing —
  see `order-placement-idempotency`.
- **You need alerting.** `StartLimitAction=` defaults to `none`, so a unit that
  exhausts its restart budget sits in `failed` silently until someone looks.
  Wire `OnFailure=` to a notifier; this skill will not tell you the bot is down.

## Prerequisites

- **Linux with systemd** and cgroup v2 for the memory directives —
  systemd.resource-control(5) states these settings "control the memory
  controller in the unified hierarchy".
- **Root (or a user manager)** to install the unit and run `systemctl
  daemon-reload`.
- **`$NOTIFY_SOCKET` in the bot's environment**, which systemd exports for
  `Type=notify`. Its absence is not an error: sd_notify(3) specifies that when
  `$NOTIFY_SOCKET` is unset "no status message could be sent, 0 is returned",
  and every notify call in `scripts/supervision_helper.py` returns `False`
  rather than raising, so the same code runs under a debugger.
- **A measured worst-case order-unwind time.** `TimeoutStopSec=` is a number you
  should be able to defend; the default is not.
- Python 3.8+. Standard library only — no dependencies.

## Workflow

1. **Put the start rate limit in `[Unit]`, and check the arithmetic.**
   - `StartLimitIntervalSec=` and `StartLimitBurst=` are `[Unit]` options.
     systemd's directive table keeps `Service.StartLimitInterval`,
     `Service.StartLimitBurst` and `Service.StartLimitAction` only "for
     compatibility, they moved into Unit" — and it has **no**
     `Service.StartLimitIntervalSec` at all. Writing the current spelling under
     `[Service]` is an unknown key: ignored, leaving the unit on
     `DefaultStartLimitIntervalSec`, which is **10s**.
   - Then check that the limit is reachable. Restarts are spaced by
     `RestartSec=`, so `StartLimitBurst` attempts span at least
     `RestartSec × (StartLimitBurst − 1)`. If that is not shorter than the
     window, the limiter can never trip. `RestartSec=10` with `burst=5` needs
     more than 40s of window — and gets 10s if the interval landed in the wrong
     section. The engine reports this as `START_LIMIT_UNREACHABLE`.
   - Decide what happens when it *does* trip: the unit stays `failed` until
     `systemctl reset-failed`. That is correct, and silent. Add `OnFailure=`.

2. **Make the watchdog reachable before you make it strict.**
   - `NotifyAccess=` "Takes one of none (the default), main, exec or all", and
     with `none` "all status update messages are ignored". `Type=notify` (or
     `notify-reload`) rescues this: "If `NotifyAccess=` is missing or set to
     none, it will be forcibly set to main." Any other type plus `WatchdogSec=`
     and no explicit `NotifyAccess=` means every ping is discarded and systemd
     terminates the bot once per interval. Reported as
     `WATCHDOG_PINGS_IGNORED`, and it is `CRITICAL` because the unit looks
     completely ordinary.

3. **Run the pre-market gate in `ExecStartPre=`, and let it fail only on faults.**
   - `run_premarket_healthcheck()` checks credentials, broker reachability and
     the exchange calendar. It fails **closed**: a calendar lookup that raises
     is a fault, not an implicit "market open".
   - Ask the calendar about the *exchange's* date, not the host's. Pass
     `as_of_date` or `exchange_timezone`. A UTC host asking an IST or
     US/Eastern calendar near midnight silently answers the wrong day, in both
     directions.
   - Give `broker_connectivity_fn` its own timeout. A hanging probe burns the
     whole `TimeoutStartSec=` budget before the unit fails.

4. **Send `READY=1` last, after the broker session is authenticated.**
   - systemd holds the unit in `activating` until it arrives, and holds
     everything ordered `After=` this unit with it. Check the return value: a
     bot that believes it announced readiness but did not is killed at
     `TimeoutStartSec`.

5. **Ping from the trading loop, on the cadence systemd gave you.**
   - Read the interval, do not hard-code it: `watchdog_ping_interval_seconds()`
     derives it from `$WATCHDOG_USEC`, which sd_watchdog_enabled(3) recommends
     pinging at "every half of the time returned here". Hard-coding 15s means an
     operator who lowers `WatchdogSec=` to 10 in a drop-in has scheduled the
     bot's execution.
   - Use `notify_watchdog_if_progressing(last_progress_monotonic,
     max_stall_seconds)`. The loop stamps `time.monotonic()` each iteration; the
     pinger refuses to vouch for a stale stamp. Monotonic, not wall-clock, so an
     NTP step cannot fake progress.

6. **On SIGTERM: `STOPPING=1` first, then unwind, then extend if you must.**
   - Send `STOPPING=1` as shutdown begins, then cancel resting orders. If
     cancellation is still progressing as `TimeoutStopSec=` approaches, send
     `EXTEND_TIMEOUT_USEC=` — each message buys another window. The alternative
     is SIGKILL mid-unwind with live orders at the broker.

7. **Handle a market holiday as a clean exit, not a failed unit.**
   - `HealthCheckResult.is_fault` is false on a holiday even though `passed` is
     false. Exiting non-zero from `ExecStartPre=` on a holiday marks the unit
     `failed` and spends a start-limit slot for a day when nothing is wrong.
   - Do the holiday check in the **main process**: send `READY=1`, see the
     calendar is closed, exit 0. `Restart=on-failure` respects a clean exit and
     the unit goes `inactive`. Do **not** reach for `SuccessExitStatus=` on the
     `ExecStartPre` — it is documented for "the main service process".

8. **Audit the effective unit, not the file you wrote.**
   - `validate_unit_file_content(systemctl_cat_output)` returns findings ordered
     most-severe-first. Branch on `finding.code`; wording may change between
     versions.

> Full step-by-step procedure and the shutdown sequence: see `references/workflows.md`.
> Directive-by-directive sources: see `references/standards.md`.
> Printable sign-off checklist: see `assets/checklist.md`.

## Common Pitfalls

- **`StartLimitIntervalSec=` under `[Service]`.** systemd has no such key in
  that section. It is ignored, the window collapses to the 10s default, and with
  a multi-second `RestartSec=` the burst limit becomes unreachable — so the unit
  crash-loops against a broker outage exactly as if you had never configured a
  limit. This is the failure a rate limit is supposed to prevent, produced by a
  rate limit that looks configured. It shipped in this skill's own reference
  unit until v2.0.0.
- **A start limit that is arithmetically unreachable.** `RestartSec=30` with
  `StartLimitBurst=5` and `StartLimitIntervalSec=60` never trips: five attempts
  need at least 120s. Present, parsed, honoured, useless.
- **`WatchdogSec=` on a non-notify type.** `NotifyAccess=` defaults to `none`,
  every `WATCHDOG=1` is discarded, and the bot is killed once per interval. The
  journal shows a watchdog timeout on a bot that was pinging correctly the whole
  time.
- **Moving the ping to its own thread to "fix" missed deadlines.** That thread
  stays healthy precisely when the trading loop wedges, which converts the
  watchdog from a liveness check on the strategy into a liveness check on the
  pinger. Gate the ping on loop progress instead, and put a timeout on the
  blocking call that caused the miss.
- **Hard-coding the ping interval.** 15s is right for `WatchdogSec=30` and fatal
  for `WatchdogSec=10`. Read `$WATCHDOG_USEC`.
- **`READY=1` before the broker session authenticates.** systemd marks a broken
  service healthy and releases every unit ordered after it.
- **Interpolating broker text into `STATUS=`.** sd_notify(3) passes "a
  single-line UTF-8 status string"; a newline in an error message appends a real
  protocol field to the datagram. The wrappers here sanitise, and
  `build_notify_message()` refuses outright.
- **`Restart=always` on a bot that can decide to stop.** A clean exit for a
  holiday, a kill switch or a decommission is undone immediately. `on-failure`
  still covers non-zero exits, fatal signals and watchdog timeouts.
- **Treating a market holiday as a start failure.** It burns a restart slot,
  leaves the unit `failed`, and pages someone for a closed exchange.
- **A `TimeoutStopSec=` shorter than a real unwind.** When it expires the
  process "will be forcibly terminated by SIGKILL" — mid-cancellation, with
  orders still live. Measure it, and send `EXTEND_TIMEOUT_USEC=`.
- **Auditing the file instead of the effective unit.** A drop-in adding
  `Restart=always` is invisible to a check that reads `/etc/systemd/system/
  trading-bot.service`.
- **Assuming the start limit failure is loud.** `StartLimitAction=` defaults to
  `none`. The bot is down, the unit is `failed`, and nothing has told anyone.

## Verification

- Run the unit suite:
  `python -m unittest discover -s skills/systemd-supervision-for-trading-bots/scripts`
  — all tests must pass. It needs no systemd, no root and no `AF_UNIX`.
- Audit the shipped `scripts/trading-bot.service` and confirm `is_valid` is True
  with no findings.
- Move `StartLimitIntervalSec=` from `[Unit]` to `[Service]` and confirm
  **both** `START_LIMIT_IGNORED_IN_SERVICE_SECTION` and, once the window
  collapses to the 10s default, `START_LIMIT_UNREACHABLE` are reported.
- Change `Type=notify` to `Type=simple` and confirm `WATCHDOG_PINGS_IGNORED`
  fires at `CRITICAL`; add `NotifyAccess=main` and confirm it clears.
- Call `notify_stopping("a\nMAINPID=1")` against a recording transport and
  confirm the payload still has exactly two lines.
- Construct with `env={"WATCHDOG_USEC": "10000000"}` and confirm
  `watchdog_ping_interval_seconds()` returns 5.0, not 15.
- Call `notify_watchdog_if_progressing(t, 20.0, now=t+20.001)` and confirm no
  ping is sent — a stalled loop must not be vouched for.
- Run the healthcheck with `is_holiday_fn` returning True and confirm
  `passed is False` while `is_fault is False`.
- On a real host: `systemd-analyze verify ./trading-bot.service`, then
  `systemctl cat trading-bot.service` and audit *that*. Confirm
  `systemctl show -p StartLimitIntervalSec,StartLimitBurst trading-bot.service`
  reports the values you intended — this is the check that catches the
  wrong-section defect on the running manager.

## Related Skills

- `graceful-shutdown-draining-in-flight-ticks`
- `kill-switch-and-drawdown-circuit-breakers`
- `execution-algorithm-kill-switch-integration`
- `order-placement-idempotency`
- `global-exchange-holiday-calendar-handling`
- `infrastructure-as-code-for-trading-hosts`
- `immutable-infrastructure-for-trading-bots`
- `centralized-secrets-management-vault-integration`
- `log-aggregation-and-centralized-observability`
- `on-call-rotation-and-escalation-for-trading-systems`
- `strategy-decommissioning-and-position-unwind-procedure`
