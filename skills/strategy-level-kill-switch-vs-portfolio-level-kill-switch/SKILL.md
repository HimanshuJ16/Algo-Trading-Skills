---
name: strategy-level-kill-switch-vs-portfolio-level-kill-switch
description: >-
  Two-tier circuit breaker deciding which scope must stop — isolating one strategy on its own drawdown, escalating to a master portfolio halt on fund drawdown or a cascade of strategy failures, failing closed on unevaluable equity without liquidating, latching every trip, and gating recovery behind a scope-aware audited human re-enable.
domain: Risk Management & Circuit Breakers
subdomain: Hierarchical Kill Switch Governance
tags: ["strategy-kill-switch", "portfolio-kill-switch", "circuit-breaker", "drawdown-limit", "cascade-failure", "mifid-ii-rts-6", "risk-governance"]
brokers_frameworks: ["MiFID II RTS 6 (EU 2017/589)", "SEC Rule 15c3-5", "Nasdaq Rule 6130 Kill Switch", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a multi-strategy book needs automated risk controls at **two scopes** and you have to decide which one fires. A strategy that breaks on a regime shift must be isolated before it damages the fund; the fund must still be able to halt everything when the aggregate is bleeding or when several strategies fail together. This engine owns that decision — per-strategy drawdown isolation, master portfolio drawdown, cascade detection, and the latches that keep both scopes stopped until a human clears them.

The regulatory shape of the two tiers is real, though no rule prescribes a number. MiFID II RTS 6 Art. 15(3) requires "repeated automated execution throttles which control the number of times an algorithmic trading strategy has been applied", after which "the trading system shall be automatically disabled until re-enabled by a designated staff member" — a *per-strategy* automatic disable with a human-gated resume. Art. 15(5) requires controls "on exposures to individual clients, financial instruments, traders, trading desks **or the investment firm as a whole**" — the firm-wide tier. Art. 12(3) requires the firm to identify which trading algorithm owns each order, which is the attribution a strategy-scoped kill depends on. See `references/standards.md`.

## When NOT to Use

- **As the order gate or the cancel dispatcher.** This engine decides *which scope must stop*. It cancels no orders, flattens no positions and inspects no order. Latching order entry and sending FIX `OrderMassCancelRequest` is `execution-algorithm-kill-switch-integration`; enforcing reduce-only flow while halted — so the halt does not veto its own liquidation — is `kill-switch-and-drawdown-circuit-breakers`.
- **As a source of regulatory thresholds.** The `10%` / `15%` / 3-strategy defaults are *your* risk policy. Nothing surveyed in `references/standards.md` sets a drawdown number or a cooldown duration for a trading firm: RTS 6 Art. 15(4) requires only that limits be set from the firm's own capital base and risk tolerance, and SEC Rule 15c3-5 binds broker-dealers with market access, not the end trading firm. Calibrate with `risk-limit-calibration-against-historical-drawdowns`.
- **As the single portfolio NAV stop.** If you need one aggregate stop with daily *and* peak-to-trough limits, NAV valuation modes and capital-flow handling, that is `portfolio-level-stop-loss-independent-of-strategy-stops`. This skill's portfolio tier is deliberately thin — its job is escalation from the strategy tier.
- **Inside a backtest.** A latching kill switch applied to a simulated equity curve truncates the drawdown tail and flatters the result. Model it as an explicit strategy rule — see `lookahead-bias-elimination`.
- **On an un-normalized multi-currency book.** Every equity figure is compared arithmetically; no FX conversion happens here. Normalize first via `multi-currency-pnl-and-fx-conversion`.
- **As a reconciliation layer.** The engine never checks that the strategy equities sum to the portfolio equity, and never contacts a broker. Source both from the broker/custodian account state, not the bot's internal bookkeeping — RTS 6 Art. 17(3) frames that duty for EU investment firms.

## Prerequisites

- Python 3.8+, standard library only.
- Per-strategy equity state (`StrategyState`: `strategy_id`, `peak_equity_usd`, `current_equity_usd`, `drawdown_limit_pct`), with `strategy_id` unique — a duplicate is rejected at construction rather than silently shadowing a monitored strategy.
- Master portfolio equity state (`PortfolioState`: `total_peak_equity_usd`, `total_current_equity_usd`, `portfolio_drawdown_limit_pct`, `max_tripped_strategies_limit`).
- All drawdown limits in **percentage points** (`10.0` is 10%, not `0.10`), all equity figures in one reporting currency, all sourced from the broker.
- A record of every **settled** capital flow per strategy and for the fund, passed as `capital_flow_usd`, so an allocation change is not read as P&L.
- A risk loop calling both evaluators on a timer, structurally separate from strategy signal generation.
- An authorised human path to `human_re_enable()`. Nothing re-arms on a timer.

## Workflow

1. **Evaluate each strategy against its own limit — `evaluate_strategy_kill_switch(strategy_id, equity, action, capital_flow_usd)`.**
   - Drawdown, measured net of settled flows: $\text{DD}_{\text{strat}} = \frac{\text{Peak}_{\text{strat}} - (\text{Equity} - F)}{\text{Peak}_{\text{strat}}}$, clamped at zero. The engine ratchets `peak_equity_usd` on the flow-adjusted equity, so a top-up cannot raise the high-water mark and a withdrawal cannot manufacture a drawdown.
   - **Decision point — breach is `>=`, not `>`.** A drawdown that reaches the limit has breached it.
   - **Decision point — a strategy trip halts that strategy and nothing else.** Its siblings keep trading; that isolation is the entire reason the strategy tier exists.
2. **Fail closed before measuring anything, and do not liquidate on it.**
   - A `NaN` equity makes `dd >= limit` False, so an unchecked non-finite input turns the breaker off while reporting healthy. Non-finite inputs, a non-numeric input and a non-positive peak all return `HALTED_INVALID_INPUT` with `is_trading_halted=True`.
   - **Decision point — a halt blocks new risk but flattens nothing.** The engine has no evidence the book is down; market-flattening on one bad tick is itself the loss event. Escalate to a human.
   - **Decision point — a halt never advances the cascade counter.** One dead feed halts every strategy simultaneously. If halts counted as strategy failures, a data outage would cascade-liquidate a fund that never lost a cent.
3. **Evaluate the master tier — `evaluate_portfolio_kill_switch(total_equity, action, capital_flow_usd)`.** Two independent triggers: fund drawdown $\ge$ `portfolio_drawdown_limit_pct`, or a cascade of strategies that each tripped on their *own* measured drawdown reaching `max_tripped_strategies_limit`.
   - **Decision point — the cascade counter excludes this switch's own fan-out.** A master trip marks every strategy tripped; if those counted, the master switch would permanently re-justify itself and re-trip the instant an operator cleared it.
   - **Decision point — the fan-out skips already-latched strategies.** A strategy already hard-liquidated on its own trip is left with its original action and originating scope intact, and is *not* queued for a second liquidation. `affected_strategies` lists only what this report newly halted.
4. **Dispatch on `is_newly_tripped`, never on `is_triggered`.** `is_triggered` is latch-inclusive and stays True for as long as the switch is engaged; a 1-second risk loop gating liquidation on it fires a fresh cascade every second while the first is still working. `is_newly_tripped` is True exactly once per trip, including under concurrent evaluation.
5. **Gate order flow on `is_trading_halted` / `is_strategy_trading_halted(strategy_id)`.** For a strategy this is True when the strategy itself is latched *or* the master switch is engaged (`PORTFOLIO_HALT_INHERITED`). The hierarchy propagates downward only: a strategy trip never halts its siblings, a fund halt always halts every strategy.
6. **Recover strategies first, then the fund — `human_re_enable(scope, operator_id, reason, strategy_id=...)`.** It refuses a blank identity, a blank reason, an operator outside the roster and a re-enable inside the `cooldown_seconds` dwell, returns a boolean the caller must check, and appends every attempt — granted *and* refused — to `re_enable_log`.
   - **Decision point — clear each strategy latch before the master latch.** A `PORTFOLIO_LEVEL` re-enable is refused while the cascade condition still holds, because lifting it then re-trips the fund on the very next evaluation. A `STRATEGY_LEVEL` re-enable *is* permitted inside a halted fund, and is safe: the inherited portfolio halt still gates that strategy, so nothing resumes trading until the fund does.
   - **Decision point — the cooldown gates a human decision; it never resumes trading by itself.** No rule surveyed mandates a duration, and an expired timer is not evidence that the condition cleared.
   - **Decision point — re-enabling clears the latch, not the breach.** The high-water mark survives, so the next evaluation re-trips unless the operator deliberately re-baselines `peak_equity_usd`. Never re-baseline automatically — that erases the limit while appearing to satisfy it.
7. **Persist every `KillSwitchExecutionReport` and every `re_enable_log` entry.** All engine state is in memory; a restart silently drops every latch. Persist it, or fail closed on start-up.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Recomputing the trip from live equity every poll.** A kill switch whose `is_triggered` is derived from the current drawdown un-kills itself the moment price bounces. The trip must latch and survive a full recovery, a flat book and the next trading day.
- **Gating the liquidation on `is_triggered`.** It stays True while latched, so every poll re-issues the full liquidation into a falling market. Dispatch on `is_newly_tripped`.
- **Trusting an unchecked `NaN`.** Every comparison against `NaN` is False, so a stale mark or a zero-denominator P&L turns the strategy limit *and* the portfolio limit off simultaneously, and the engine reports healthy while checking nothing.
- **Auto-liquidating on data you could not evaluate.** Force-flattening because equity was unreadable converts a feed outage into a realized loss at market. Halt new risk, page a human, do not sell.
- **Letting fail-closed halts feed the cascade counter.** One dead feed halts every strategy at once; counted as strategy failures, that trips the master switch and liquidates a fund that is perfectly healthy. This is the single most dangerous coupling between the two tiers.
- **Letting the master switch's own fan-out feed the cascade counter.** The portfolio trip marks all strategies tripped, so the cascade condition becomes permanently true and the master switch re-trips the instant it is cleared.
- **Overwriting an already-tripped strategy's action and scope during fan-out.** It destroys the audit record of why that strategy first stopped, and re-queues it for a second liquidation of a position that is already flat.
- **Confusing the scopes in the other direction.** Tripping the master switch and liquidating the whole fund because one small sub-strategy breached its own 10% limit. Isolation is the strategy tier's job.
- **Reporting `SOFT_HALT` for a healthy strategy.** Versions before 2.0.0 returned `SOFT_HALT` whenever `is_triggered` was False, so a caller reading `report.action` without also checking `is_triggered` halted a strategy that never breached anything. A no-breach report now carries `NO_ACTION`.
- **Passing a limit as a fraction.** `drawdown_limit_pct=0.10` meaning "10%" reads here as **0.1%** and liquidates on noise — the mirror image of the fraction-based engines elsewhere in this library, and just as silent. The units here are percentage points.
- **Setting `max_tripped_strategies_limit` without reference to the roster.** A limit above the number of registered strategies can never fire; a limit of 3 in a 3-strategy fund means the cascade trigger only fires once everything is already dead.
- **Reading a settled allocation change as drawdown.** A routine capital withdrawal from a strategy trips its kill switch and market-flattens a book that was never in trouble; a top-up ratchets the peak and understates every subsequent real drawdown.
- **Auto-resuming when a cooldown expires.** A timer clears no bug and no market regime. The dwell gates a human decision; it does not substitute for one.
- **Lifting the master latch while the cascade condition still holds**, then being surprised when the fund re-trips on the next evaluation. Clear the strategy latches first.
- **Check-then-act with no lock.** A kill switch lives in a concurrent path; two threads both reading `is_tripped == False` produce two liquidation cascades.
- **Treating in-memory latches as durable.** A process restart re-arms every strategy and the fund with no record that anything was ever halted.

## Verification

- Instantiate `HierarchicalKillSwitchEngine` with three $100,000 strategies in a $300,000 fund (10% / 15% limits, `max_tripped_strategies_limit=2`). Drop `STAT_ARB` to $88,000 (12% DD) $\implies$ verify `is_newly_tripped`, `affected_strategies == ["STAT_ARB"]`, and that `MOMENTUM` and `MEAN_REVERSION` stay untripped and unhalted.
- Re-evaluate `STAT_ARB` at a fully recovered $100,000 $\implies$ verify `drawdown_pct == 0.0` but `is_triggered` and `is_trading_halted` still True, `is_latched` True, `is_newly_tripped` False.
- Evaluate the same breaching state five times $\implies$ verify exactly one `is_newly_tripped` and exactly one entry across all `affected_strategies`; repeat with eight concurrent threads and confirm the same.
- Drop fund equity to $240,000 (20% DD vs 15%) $\implies$ verify the master switch trips and all three strategies are halted.
- Feed a `NaN` equity $\implies$ verify `HALTED_INVALID_INPUT`, `is_trading_halted=True`, `affected_strategies == []` and `action == NO_ACTION` — not a healthy report and not a liquidation. Repeat for `Inf`, `None`, a numeric string, and a non-positive `peak_equity_usd`.
- Halt all three strategies with `NaN` $\implies$ verify `cascade_trip_count == 0` and that a subsequent portfolio evaluation on healthy equity does **not** trip.
- Trip two strategies on their own drawdown with fund equity at only 2% DD $\implies$ verify `CASCADE_BREACH`. Then trip the master switch on drawdown alone $\implies$ verify `cascade_trip_count == 0` afterwards, so the cleared switch does not immediately re-trip.
- Trip `STAT_ARB` with `SOFT_HALT`, then trip the master switch with `HARD_LIQUIDATE` $\implies$ verify `STAT_ARB` keeps `action_taken == SOFT_HALT` and `tripped_by_scope == STRATEGY_LEVEL`, is absent from `affected_strategies`, and that the fanned-out strategies carry a non-zero `tripped_time_epoch`.
- Halt the fund with a `NaN` (no fan-out), then evaluate a healthy strategy $\implies$ verify `is_triggered` False but `is_trading_halted` True with `PORTFOLIO_HALT_INHERITED`.
- Evaluate at exactly the limit ($90,000 on a $100,000 peak) $\implies$ verify a breach; at $90,000.40 $\implies$ verify `drawdown_pct` reports `10.0` after rounding while `is_triggered` stays False.
- Withdraw $20,000 from a flat $100,000 strategy with `capital_flow_usd=-20_000` $\implies$ verify 0% drawdown and an unchanged peak; deposit $50,000 into a strategy that then loses $12,000 $\implies$ verify a 12% drawdown and an unchanged peak.
- Construct with `drawdown_limit_pct` of `0`, `-5`, `150`, `NaN` or `None`, with `max_tripped_strategies_limit` of `0` or `2.5`, with a negative cooldown, or with duplicate `strategy_id`s $\implies$ verify each raises `ValueError`. Construct with `0.10` $\implies$ verify a warning naming the fraction trap.
- Pass `equity=1e308` with `capital_flow_usd=-1e308` $\implies$ verify `HALTED_INVALID_INPUT` and that `peak_equity_usd` was **not** ratcheted to infinity, which would halt the strategy forever.
- Call either evaluator with `action=NO_ACTION`, an unknown action, `None`, or an unhashable value $\implies$ verify `ValueError` (never a bare `TypeError` escaping into the risk loop) and that nothing tripped.
- Confirm `human_re_enable()` refuses a blank identity, a blank reason, an unlisted operator, an untripped scope, an unknown scope, a missing `strategy_id`, and a request inside the cooldown; that every attempt lands in `re_enable_log`; and that a `PORTFOLIO_LEVEL` request is refused while the cascade condition holds.
- Confirm a `PORTFOLIO_LEVEL` re-enable releases only the strategies the master switch itself halted; that a `STRATEGY_LEVEL` re-enable inside a halted fund leaves `is_strategy_trading_halted()` True; and that re-enabling without re-baselining the peak re-trips on the next evaluation.
- Run `python -m unittest discover -s skills/strategy-level-kill-switch-vs-portfolio-level-kill-switch/scripts` — 51 tests, 100% pass rate.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `portfolio-level-stop-loss-independent-of-strategy-stops`
- `execution-algorithm-kill-switch-integration`
- `emergency-manual-override-access-control`
- `risk-limit-calibration-against-historical-drawdowns`
- `risk-limit-breach-escalation-matrix`
- `multi-strategy-capital-allocation-limits`
- `risk-control-latency-budget`
