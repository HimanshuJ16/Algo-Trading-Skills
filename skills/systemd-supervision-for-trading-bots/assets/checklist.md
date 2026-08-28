# Pre-Flight Checklist — systemd Supervision for Trading Bots

Sign off against the **effective** unit (`systemctl cat <unit>`), not the file
in your repository. Drop-ins override it.

## Start rate limiting

- [ ] `StartLimitIntervalSec=` and `StartLimitBurst=` are in the **`[Unit]`**
      section. (`StartLimitIntervalSec=` under `[Service]` is an unknown key:
      ignored, silently falling back to the 10s default.)
- [ ] `systemctl show -p StartLimitIntervalSec,StartLimitBurst <unit>` reports
      the values you intended, not the manager defaults.
- [ ] The limit is arithmetically reachable:
      `RestartSec × (StartLimitBurst − 1) < StartLimitIntervalSec`.
- [ ] `OnFailure=` points at a notifier — exhausting the limit leaves the unit
      `failed` and silent (`StartLimitAction=` defaults to `none`).
- [ ] The team knows that recovery needs `systemctl reset-failed`, and knows not
      to run it before diagnosing why the budget was spent.

## Restart policy

- [ ] `Restart=on-failure`, not `always`. A clean exit (holiday, kill switch,
      decommission) must stay down.
- [ ] `RestartSec=` is long enough not to hammer a degraded broker, and short
      enough that the rate limit can still be reached.

## Watchdog

- [ ] `Type=notify` (or `notify-reload`), **or** an explicit `NotifyAccess=`.
      Otherwise `NotifyAccess=` is `none` and every `WATCHDOG=1` is discarded
      while systemd terminates the bot once per interval.
- [ ] `WatchdogSec=` is set, and `systemctl show -p WatchdogUSec <unit>` agrees.
- [ ] The ping interval is **derived from `$WATCHDOG_USEC`**, not hard-coded.
- [ ] The ping is gated on trading-loop progress — no bare timer thread that
      keeps pinging while the loop is wedged.
- [ ] Blocking calls in the loop have their own timeouts, sized well under
      `WatchdogSec=`.

## sd_notify protocol

- [ ] `READY=1` is sent **after** broker authentication and position
      reconciliation, and its return value is checked.
- [ ] `STOPPING=1` is sent at the start of shutdown, before order cancellation.
- [ ] No caller-supplied or broker-supplied text is interpolated into `STATUS=`
      without sanitising — `STATUS=` is a single-line field and a newline
      appends a further protocol field.
- [ ] The bot runs correctly with `$NOTIFY_SOCKET` unset (under a debugger, in
      a container), treating every notify call as a no-op.

## Pre-market gate

- [ ] `ExecStartPre=` runs the healthcheck and exits non-zero **only** on a
      fault (`HealthCheckResult.is_fault`), never on a market holiday.
- [ ] The holiday check asks about the **exchange's** date — `as_of_date` or
      `exchange_timezone` is passed, not the host's local date.
- [ ] `broker_connectivity_fn` has its own timeout; it does not rely on
      `TimeoutStartSec=` as its bound.
- [ ] A calendar lookup failure fails closed (does not start), rather than being
      read as "market open".
- [ ] Secrets are checked for presence only, and are never logged.

## Shutdown

- [ ] `TimeoutStopSec=` is set explicitly and derived from a **measured**
      worst-case order unwind, not inherited.
- [ ] A SIGTERM handler cancels resting orders and sends `EXTEND_TIMEOUT_USEC=`
      while cancellation is still progressing — extending conditionally, not
      unconditionally.
- [ ] A shutdown that hits SIGKILL has been rehearsed: you know which orders
      would be left live at the broker and how the next start reconciles them.

## Resource limits

- [ ] `MemoryHigh=` (throttle) and `MemoryMax=` (ceiling) are set, sized from
      measured RSS under peak subscription load.
- [ ] The host runs cgroup v2; otherwise these directives do nothing.
- [ ] Someone has decided whether an OOM kill mid-order is acceptable, and what
      the restart does about the position it leaves behind.

## Verification

- [ ] `systemd-analyze verify ./<unit>.service` is clean.
- [ ] `validate_unit_file_content(systemctl_cat_output)` returns
      `is_valid is True`.
- [ ] `python -m unittest discover -s skills/systemd-supervision-for-trading-bots/scripts`
      passes.
- [ ] The audit runs in CI **and** on the host after deploy — the two can
      disagree, and the host is the one that trades.
