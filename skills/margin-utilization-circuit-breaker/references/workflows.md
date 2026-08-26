# Deep Workflow Reference — margin-utilization-circuit-breaker

## Full Procedure

### 1. Choose the basis, and record the choice

Decide whether `used_margin` will carry the **maintenance** requirement (what the broker
liquidates against) or the **initial** requirement (what it takes to open). Pass it as
`basis=`. The two differ by roughly 2:1 for US margin equities — Reg T initial 50% versus
FINRA 4210(c) maintenance 25% — so the same threshold is a very different budget on each.
Default to `MAINTENANCE`: that is the basis on which forced liquidation happens.

### 2. Map broker fields deliberately

| Broker | Numerator (`used_margin`) | Denominator (`account_equity`) | Watch for |
|---|---|---|---|
| Interactive Brokers | `MaintMarginReq` (or `FullInitMarginReq` on the INITIAL basis) | `NetLiquidation` | IBKR's own cushion uses `EquityWithLoanValue` in the numerator and `NetLiquidation` in the denominator; the two equity measures differ when the account holds non-marginable value. Cross-check `ExcessLiquidity` via `broker-account-margin-call-handling` |
| Alpaca | `maintenance_margin` or `initial_margin` | `equity` | Both requirement fields are exposed separately and are continuously updated; the basis is a free, explicit choice |
| Zerodha Kite Connect | `utilised.debits` | *not* `net` | `debits` bundles realised and unrealised M2M with SPAN, exposure and premium; `net` is a cash balance, not equity. No maintenance figure is exposed |
| MetaTrader 4/5 | `AccountInfoDouble(ACCOUNT_MARGIN)` | `ACCOUNT_EQUITY` | **Never** pass `ACCOUNT_MARGIN_LEVEL` — it is `equity / margin × 100`, the reciprocal, where a *low* number is the dangerous one |

Do the conversion in the broker adapter. The breaker takes two numbers and a timestamp; it
cannot detect that they arrived from the wrong fields.

### 3. Poll, timestamp, and fail closed

```python
from datetime import datetime, timezone
from margin_breaker import MarginBasis, MarginDataError, MarginUtilizationBreaker

breaker = MarginUtilizationBreaker(
    warning_threshold=0.60,
    hard_stop_threshold=0.80,
    basis=MarginBasis.MAINTENANCE,
    max_data_age_seconds=15.0,
)

snapshot = broker.account_summary()          # your adapter
try:
    state = breaker.evaluate_margin(
        used_margin=snapshot.maintenance_margin,
        account_equity=snapshot.net_liquidation,
        as_of=snapshot.read_at,              # timezone-aware
    )
except MarginDataError:
    halt_trading_and_page_a_human()          # a data outage is a halt, not a pass
```

`as_of` must be timezone-aware. A naive timestamp is silently read as local time, which
skews the freshness check by the UTC offset — and in the tolerant direction for offsets
behind UTC. A timestamp dated in the future is rejected outright: clock skew that way makes
every snapshot look fresh forever.

### 4. Gate orders on the projected requirement

```python
check = breaker.check_order(
    used_margin=snapshot.maintenance_margin,
    account_equity=snapshot.net_liquidation,
    additional_margin_required=whatif.maint_margin_change,   # signed
    as_of=snapshot.read_at,
)
if not check.approved:
    if check.is_data_error:
        escalate_feed_outage(check.rejection_reason)
    else:
        record_veto(check.rejection_reason)
    return
place(order)
```

`additional_margin_required` is a **signed delta**, not an absolute requirement: positive
when the order consumes margin, negative when it releases it. At IBKR, submit the order
with `Order.whatIf = True` and read `maintMarginChange` / `initMarginChange` from the
returned `OrderState` — matching the basis you configured. Estimating this number yourself
is the weak link in the chain; get it from the broker where you can.

`check_order` never raises. Unusable input returns `approved=False` with
`is_data_error=True`, so a broken feed fails closed rather than becoming an approval.

### 5. Understand exactly what gets through while halted

An order is approved while the breaker is halted **only** when the projected requirement is
*strictly less* than the current one:

| Situation | `additional_margin_required` | Outcome |
|---|---|---|
| Full close | $-90{,}000$ from $90{,}000$ used | Approved, `risk_reducing=True` |
| Partial reduction still over the limit | $-5{,}000$ from $90{,}000$ used, projecting $0.85$ | Approved, `risk_reducing=True` — direction is what matters |
| Margin-neutral swap or reversal | $0$ | **Rejected.** Equality is not reduction |
| Any new exposure | $> 0$ | Rejected |
| Releases more than is used | $-25{,}000$ from $10{,}000$ used | Rejected as a data error — the projection is impossible |

The determination is arithmetic on the projected requirement. There is no "this is a
closing order" flag to pass, and deliberately so: a bypass flag that is trusted
unconditionally lets a margin-increasing order through every gate.

Note the limitation under portfolio-level margining. Under Portfolio Margin or SPAN,
closing one leg of a hedge can *raise* the total requirement. The breaker will correctly
see that as a non-reducing order and block it — which is the safe answer, but means the
unwind must be sequenced so each step genuinely reduces the requirement. Sizing that
sequence is `broker-account-margin-call-handling`'s job, not this module's.

### 6. Re-arm as a governed event

```python
cleared = breaker.re_arm(
    operator="alice@firm.example",
    reason="Reduced ES exposure by 4 lots; utilization 48%; reviewed with risk at 14:05Z",
    used_margin=snapshot.maintenance_margin,
    account_equity=snapshot.net_liquidation,
    as_of=snapshot.read_at,
)
if not cleared:
    # The refusal is already in breaker.re_arm_log with its reason.
    return
```

Refused on a blank operator, a blank reason, unusable or stale input, or utilization still
above `re_arm_threshold`. **Check the boolean** — an unchecked re-arm reads as a granted
one. Persist `re_arm_log` alongside your other risk-control audit records; see
`risk-control-bypass-audit-logging`.

The re-arm threshold defaults to `warning_threshold`, so an account must come back into the
pre-warning band before it can resume. Re-arming at the trip level re-trips on the next
poll, and the one-evaluation reprieve reads to an operator like a fix.

### 7. Operate it

- **Poll cadence** must be tighter than `max_data_age_seconds`, with margin left for a
  slow response. Both numbers are policy, not defaults to inherit.
- **Concurrency**: the latch is guarded by an `RLock`, so concurrent evaluation latches
  exactly once. It does not make check-then-place atomic — serialise that at the caller, or
  two orders each pass the gate against the same headroom.
- **Re-check the remainder of a partially filled parent order.** The requirement consumed by
  the filled portion is already in `used_margin`; the child slice still to go is a fresh
  order and needs a fresh check.
- **Expect the requirement to move without you.** Clearing houses revise performance bonds
  by advisory notice with a stated effective date, and brokers add house margin. Alert on a
  jump in utilization that no fill explains.
- **Structural independence.** Keep the breaker out of the strategy module, so a bug in
  signal generation cannot also disable the safeguard. See
  `kill-switch-and-drawdown-circuit-breakers`.

## Production Implementation Reference

- Code: `scripts/margin_breaker.py` (`MarginUtilizationBreaker`, `MarginBasis`,
  `MarginState`, `MarginOrderCheck`, `ReArmAttempt`, `MarginDataError`).
- Tests: `scripts/test_margin_breaker.py`.
- Sources and jurisdictional limits: `references/standards.md`.
