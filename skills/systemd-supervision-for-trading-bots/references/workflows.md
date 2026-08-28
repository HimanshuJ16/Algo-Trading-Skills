# Workflows for systemd Supervision of Trading Bots

The procedure, in the order you should actually do it. Sources for every
directive quoted here are in `standards.md`.

## 1. Write the unit, with the rate limit in `[Unit]`

Start from `scripts/trading-bot.service`. The one thing not to "tidy up" is the
placement of the start rate limit:

```ini
[Unit]
StartLimitIntervalSec=600
StartLimitBurst=5
```

These are `[Unit]` options. systemd's directive table keeps
`Service.StartLimitInterval`, `Service.StartLimitBurst` and
`Service.StartLimitAction` only for compatibility, and has **no**
`Service.StartLimitIntervalSec` entry at all — so the current spelling under
`[Service]` is an unknown key, ignored, leaving the unit on
`DefaultStartLimitIntervalSec` (10s).

Then check the arithmetic, because a correctly-placed limit can still be
unreachable:

```
RestartSec × (StartLimitBurst − 1)  <  StartLimitIntervalSec
        10  ×  (5 − 1)  =  40s      <  600s        ✓ reachable
        10  ×  (5 − 1)  =  40s      <  10s         ✗ never trips
```

Decide what happens when it trips. The unit stays `failed` until `systemctl
reset-failed`, and `StartLimitAction=` defaults to `none`, so nothing tells
anyone. Wire `OnFailure=` to a notifier unit.

## 2. Verify what the manager actually loaded

Auditing the file you wrote is not the same as auditing the unit systemd runs:

```bash
systemd-analyze verify ./trading-bot.service      # syntax, unknown keys, deps
systemctl cat trading-bot.service                 # fragment + every drop-in
systemctl show -p StartLimitIntervalSec,StartLimitBurst,WatchdogUSec \
    trading-bot.service                           # the values in force
```

That last command is the one that catches the wrong-section defect: if
`StartLimitIntervalSec` was ignored, `systemctl show` reports the 10s default
rather than the 600s you wrote. Feed `systemctl cat` output — not the file on
disk — into `validate_unit_file_content()`.

## 3. Wire the pre-market gate

`ExecStartPre=` runs a wrapper around `run_premarket_healthcheck()`. Two rules:

**Exit non-zero only on a fault.** `ExecStartPre=` failure means "the unit is
considered failed", which consumes a start-limit slot and pages someone. A
closed exchange is not a fault.

**Ask about the exchange's date.** Pass `as_of_date` or `exchange_timezone`. A
UTC host asking an IST calendar at 23:00 local asks about the wrong day.

```python
import sys, datetime
from supervision_helper import SystemdSupervisionHelper

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

result = SystemdSupervisionHelper.run_premarket_healthcheck(
    secrets_dict=load_secrets(),
    broker_connectivity_fn=lambda: broker.ping(timeout=5.0),  # its own timeout
    is_holiday_fn=exchange_calendar.is_holiday,
    exchange_timezone=IST,
)

# Faults fail the unit. A holiday does not -- the main process handles that.
sys.exit(1 if result.is_fault else 0)
```

Give `broker_connectivity_fn` a timeout of its own. systemd bounds
`ExecStartPre=` with `TimeoutStartSec=`, but a probe that hangs spends that
entire budget before the unit fails, delaying the restart cycle by minutes.

## 4. Start up, then announce readiness

```python
helper = SystemdSupervisionHelper()

broker.connect()            # authenticate first
broker.subscribe(universe)  # then subscribe
state.reconcile_positions() # then reconcile what the last run left behind

if not helper.notify_ready("session live, %d symbols" % len(universe)):
    logger.warning("READY=1 was not delivered; systemd may kill us at TimeoutStartSec")
```

Order matters. `READY=1` releases every unit ordered `After=` this one, so
sending it before authentication tells systemd — and everything downstream —
that a broken service is healthy.

## 5. Ping from the loop, on systemd's cadence

Read the interval rather than hard-coding it, and gate the ping on loop
progress:

```python
interval = helper.watchdog_ping_interval_seconds()   # None if not supervised
max_stall = 20.0                                     # keep below WatchdogSec

last_progress = time.monotonic()
while running:
    process_market_data()      # the real work
    last_progress = time.monotonic()

    if interval and time.monotonic() - last_ping >= interval:
        helper.notify_watchdog_if_progressing(last_progress, max_stall)
        last_ping = time.monotonic()
```

**Do not move the ping to a bare background thread.** It is the obvious fix for
"a slow REST call made us miss the deadline", and it is wrong: the thread keeps
pinging happily while the trading loop is wedged, which is precisely the
condition the watchdog exists to catch. If you do need a separate pinger thread
(because the loop's tail latency genuinely exceeds the interval), it must still
consult the loop's progress stamp — which is what
`notify_watchdog_if_progressing()` does. Fix the underlying miss with a timeout
on the blocking call.

## 6. Shut down: STOPPING, unwind, extend, exit

```python
def on_sigterm(signum, frame):
    helper.notify_stopping("cancelling open orders")
    deadline = time.monotonic() + 25.0          # under TimeoutStopSec=30

    for order in list(open_orders):
        broker.cancel(order.id)
        if time.monotonic() > deadline:
            # Still making progress -- buy another window rather than be SIGKILLed
            helper.notify_extend_timeout(30.0)
            deadline = time.monotonic() + 25.0

    broker.disconnect()
    sys.exit(0)
```

When `TimeoutStopSec=` expires the process "will be forcibly terminated by
SIGKILL" — mid-cancellation, with orders still resting at the broker and no
local record of which ones got through. `EXTEND_TIMEOUT_USEC=` is the documented
escape, and it only helps if you send it *before* the budget runs out.

Extend only while cancellation is genuinely progressing. A loop that extends
unconditionally has replaced a bounded shutdown with an unbounded one.

## 7. Handle a market holiday as a clean exit

Do this in the main process, not in `ExecStartPre=`:

```python
helper.notify_ready("started; checking exchange calendar")
if exchange_calendar.is_holiday(datetime.datetime.now(IST).date()):
    helper.notify_stopping("exchange closed today")
    sys.exit(0)          # clean exit: Restart=on-failure leaves us alone
```

`Restart=on-failure` does not restart a clean exit, so the unit settles into
`inactive` rather than `failed`, and no start-limit slot is spent.

Do **not** reach for `SuccessExitStatus=` on the `ExecStartPre` command: the
manual documents it for "the main service process" only.

## 8. Operate it

```bash
systemctl status trading-bot.service      # STATUS= text appears here
journalctl -u trading-bot.service -f      # stdout/stderr + systemd's own events
systemctl reset-failed trading-bot.service  # after the start limit is exhausted
```

`reset-failed` is the manual override for a bot that exhausted its restart
budget. Before running it, find out why the budget was exhausted — the limit did
its job, and clearing it without a diagnosis restarts the crash loop with a
fresh allowance.

## 9. Re-audit after every change to the unit

A drop-in adding `Restart=always`, or a deploy tool that rewrites the fragment,
silently undoes the work above. Run `validate_unit_file_content()` against
`systemctl cat` output in CI **and** on the host after deploy — the two can
disagree, and the host is the one that trades.
