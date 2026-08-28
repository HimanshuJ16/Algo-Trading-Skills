# Pre-Flight / Sign-off Checklist — portfolio-level-stop-loss-independent-of-strategy-stops

Use this before considering the skill's implementation complete.

- [ ] **Independent Evaluation Loop:** Confirm `evaluate_portfolio_stop()` runs in a risk loop separate from strategy signal generation, so a strategy bug cannot stop it running.
- [ ] **Broker-Sourced NAV:** Confirm cash, positions and marks come from the broker/custodian account state, not the bot's internal bookkeeping, and that everything is in one reporting currency.
- [ ] **Correct Valuation Mode:** Confirm `nav_valuation_mode` matches the account type — `CASH_PLUS_UNREALIZED_PNL` for any margined book, because `qty × price` there is notional, not equity.
- [ ] **Limits Are Fractions:** Confirm `max_daily_drawdown_pct` / `max_peak_drawdown_pct` are fractions (`0.05` = 5%, not `5`) and that an out-of-range value raises `ValueError` at construction.
- [ ] **Limits Are Calibrated, Not Inherited:** Confirm the 5% / 10% defaults were reviewed against your own drawdown history and are recorded as firm risk policy — no regulator mandates them (`references/standards.md`).
- [ ] **Fail-Closed On Bad Data:** Confirm a `NaN`/`Inf` input or a non-positive equity baseline returns `HALTED_INVALID_INPUT` with the lockout set, rather than `PORTFOLIO_NAV_HEALTHY`.
- [ ] **Stale Marks Halt:** Confirm `max_price_staleness_s` is configured wherever the feed can go quiet, and that an over-age mark returns `HALTED_STALE_PRICES`.
- [ ] **Halts Do Not Auto-Liquidate:** Confirm both fail-closed statuses report `positions_to_flatten_count = 0` and route to a human instead of market-flattening on unevaluable data.
- [ ] **Capital Flows Adjusted:** Confirm every settled deposit/withdrawal is reported via `capital_flow_since_sod` / `capital_flow_since_peak`, and that a scheduled withdrawal does not trip the stop.
- [ ] **Breach Thresholds:** Confirm a drawdown exactly equal to a limit breaches (`>=`), and that breach flags are decided on unrounded values.
- [ ] **Flatten Fires Once:** Confirm the flatten request is emitted on the breach transition only, and that concurrent evaluations produce exactly one liquidation cascade.
- [ ] **Lockout Latches:** Confirm the lockout survives NAV recovery, a flat book and the next day's start-of-day reset, and that `is_latched` distinguishes a latched lock from a fresh breach.
- [ ] **Liquidation Is Not Self-Blocked:** Confirm the flatten routes through an order gate that permits reduce-only flow while halted (`kill-switch-and-drawdown-circuit-breakers`).
- [ ] **Human Re-Enable Gate:** Confirm `human_re_enable()` refuses a blank identity, a blank reason and an unlisted operator, returns a checked boolean, and appends every attempt to `re_enable_log`.
- [ ] **Peak Re-Baseline Is Deliberate:** Confirm resuming after a peak-drawdown halt requires an explicit, recorded operator change to `peak_equity` — never an automatic reset.
- [ ] **Out-Of-Band Alerting:** Confirm a breach and, more loudly, a *failed* flatten reach a human through a channel independent of normal logging.
- [ ] **Audit Trail Persisted:** Confirm every `PortfolioStopReport` and every `re_enable_log` entry is stored durably.
- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/portfolio-level-stop-loss-independent-of-strategy-stops/scripts` and confirm a 100% pass rate.
- [ ] **Live-Fire Drill:** Confirm each trigger — breaching NAV, `NaN` mark, stale feed, unauthorized re-enable — has been deliberately engineered in a paper/sandbox environment, not merely unit-tested.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
