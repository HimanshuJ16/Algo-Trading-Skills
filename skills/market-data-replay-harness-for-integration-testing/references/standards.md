# Standards — Market Data Replay Harness for Integration Testing

## What is actually mandated, and on whom

| Requirement | Who it binds | Status | Source |
|---|---|---|---|
| Establish "clearly delineated methodologies to develop and test" algorithmic trading systems, algorithms and strategies prior to deployment or substantial update | EU/EEA investment firms engaged in algorithmic trading | **Mandatory** | RTS 6, Art. 5(1) |
| Those methodologies must ensure the system/algorithm (a) "does not behave in an unintended manner", (b) complies with the firm's obligations, (c) complies with venue rules, (d) "does not contribute to disorderly trading conditions, continues to work effectively in stressed market conditions" and allows switching off | Same firms | **Mandatory** | RTS 6, Art. 5(4) |
| Further testing where there are "substantial changes to the algorithmic trading system or to the access to the trading venue" | Same firms | **Mandatory** | RTS 6, Art. 5(5) |
| Testing of compliance with Art. 5(4)(a), (b) and (d) must be "undertaken in an environment that is separated from its production environment" and used specifically for testing/development | Same firms | **Mandatory** | RTS 6, Art. 7(1) |
| Conformance testing against the **trading venue's** or DEA provider's own system, verifying the algorithm "interacts with the trading venue's matching logic as intended" and "adequately processes the data flows downloaded from the trading venue" | Same firms | **Mandatory** | RTS 6, Art. 6(1)–(2) |
| Annual stress test that systems and controls "can withstand increased order flows or market stresses", comprising message-volume and trade-volume tests at **twice** the highest levels of the previous six months, carried out so as not to affect the production environment | Same firms | **Mandatory** | RTS 6, Art. 10 |
| Retesting on any "material change or substantial update" — ESMA's non-exhaustive table includes *External Dependencies: replacing third-party providers or **data feeds***, and *Scope: deploying the algorithm in new instruments, venues, or asset classes* | Same firms; ESMA's supervisory expectation | Supervisory guidance — the briefing states its content "is non-binding and not subject to a 'comply or explain' mechanism" | ESMA Supervisory Briefing on Algorithmic Trading in the EU, §§30–31 (26 Feb 2026) |
| Pre-trade controls tested at design/calibration and before deployment, after any major change to the governing algorithm, and after a risk event or major change in market conditions | Same firms | Supervisory guidance | ESMA Supervisory Briefing, §88 |

Applicability boundaries that are easy to get wrong:

- **RTS 6 binds EU/EEA investment firms engaged in algorithmic trading.** There is no direct US federal analogue mandating pre-deployment algorithm testing for an ordinary broker-dealer or proprietary trading firm. SEC Rule 15c3-5 governs pre-trade risk controls, not testing methodology. Regulation SCI's systems-testing and capacity obligations (17 CFR 242.1001(a)(2)) attach to **SCI entities** — SROs, significant ATSs, plan processors and similar — not to a trading firm running a replay harness. Do not cite either at a firm they do not bind.
- **A replay harness satisfies Art. 7(1)'s *environment* requirement, not Art. 6's *conformance* requirement.** Art. 6 testing is against the venue's or DEA provider's system. A file cannot match orders.
- **Replay speed is not stress-test volume.** Art. 10 is denominated in the highest *volume* of messages and trades over the previous six months, doubled. Running a normal session at 10x multiplies the *rate*, not the six-month volume, and tells you nothing about the Art. 10 levels. See `load-testing-before-scaling-to-new-instrument-universe` for the volume projection and `chaos-engineering-for-trading-infrastructure` for failure injection.

## What a static replay structurally cannot show

The recorded book does not react to the orders the system under test emits. Every consequence of your own participation is therefore absent: market impact, queue position, adverse selection, fill probability, and any feedback loop between your orders and other participants' behaviour.

The FCA's multi-firm review is explicit about the gap, contrasting good practice — "Firms who develop (or use third party) dynamic testing environments, that not only consider how their algorithmic trading strategies perform in a period of market disruption, but also assess whether their strategy further contributes (in combination with other trading activity) to market disruption" — with poor practice: "Firms who conduct basic testing of their algorithmic trading strategies which only assess operational efficiency and focus on considerations such as their performance against certain benchmarks or the profit and loss of the strategy. In these cases, firms are unable to demonstrate the potential impact of their algorithmic trading strategies on market integrity."

A replay harness is a component of the first, never a substitute for it.

## Speed modes

| Mode | Multiplier ($S$) | What it is for | What it costs |
|---|---|---|---|
| Real-time | $S = 1.0$ | Timer, throttle, staleness and rate-limit behaviour at recorded spacing | Wall-clock duration equals the capture; sub-ms spacing is not reproducible (below) |
| Fast-forward | $S = 10.0$ to $100.0$ | CI suites, throughput probes, longer sessions in bounded time | Compresses gaps below the sleep floor; lag rises as $S$ grows |
| ASAP | `asap_mode=True`, or $S = \infty$ | Logic and order-sequence regression; the only fully reproducible mode | No timing information whatsoever |

Dispatch deadlines are absolute — $t_{\text{wall},0} + (t_i - t_0)/S$ — so callback cost does not accumulate into drift. Per-tick `sleep((t_{i+1}-t_i)/S)` scheduling does accumulate, and is the defect this skill's v2.0.0 fixed.

## Timing granularity: what a Python harness can and cannot deliver

`time.sleep()`'s own documentation states the suspension "may be longer than requested by an arbitrary amount, because of the scheduling of other activity in the system." Since CPython 3.11, Unix uses `clock_nanosleep()`/`nanosleep()` and Windows a waitable timer; the CPython docs note that on Windows 10 and newer that timer "provides resolution of 100 nanoseconds". Timer resolution is not scheduling accuracy: measured on CPython 3.11 / Windows 11, requested sleeps of 100 µs, 500 µs, 1 ms and 5 ms each overshot by roughly 300–900 µs, i.e. the floor is set by the scheduler, not by the requested duration.

Consequences, all of them assumptions this skill makes explicit rather than hiding:

- Tick spacing below roughly 1 ms cannot be reproduced in-process. `min_sleep_sec` (default 500 µs) is where the harness stops pretending and dispatches immediately, booking the shortfall as lag.
- `max_scheduling_lag_sec` and `ticks_dispatched_late` are the *measurement* of that limit for a given run, on a given machine. They are the only honest basis for a claim about replay fidelity.
- `time.perf_counter()` is used for all scheduling: it is monotonic on CPython and "does include time elapsed during sleep". `time.time()` is not used anywhere, because it is adjustable and can step backwards.

## Determinism, precisely scoped

- **ASAP mode**: dispatch order, callback sequence and emitted orders are byte-identical across runs for identical input.
- **Timed mode**: order and content are identical; wall-clock lag is not, and depends on machine load. Regression baselines must diff callback outputs, never timings.
- **Ordering**: `(timestamp, sequence_id)`. The sequence tie-break makes ties reproducible; it is not venue-aware, so merged multi-venue captures must be given one monotonic sequence space before replay. See `multi-exchange-feed-normalization` and `sequence-number-gap-detection-for-feeds`.
- **Non-finite timestamps are rejected.** NaN compares false against everything and would silently scatter the sort order.

## Sources

- Commission Delegated Regulation (EU) 2017/589 (RTS 6), Arts. 5, 6, 7, 10 — https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng (article text verified against the Commission's adopted text, https://ec.europa.eu/finance/securities/docs/isd/mifid/rts/160719-rts-6_en.pdf)
- ESMA, *Supervisory Briefing on Algorithmic Trading in the EU*, ESMA74-1505669079-10311, 26 February 2026 (§§25, 30–31 material-change table, §32 stress testing, §88 PTC testing) — https://www.esma.europa.eu/sites/default/files/2026-02/ESMA74-1505669079-10311_Supervisory_Briefing_on_Algorithmic_Trading_in_the_EU.pdf
- FCA, *Algorithmic Trading Compliance in Wholesale Markets*, February 2018, §§3.4–3.8 and §6.12 — https://www.fca.org.uk/publication/multi-firm-reviews/algorithmic-trading-compliance-wholesale-markets.pdf
- 17 CFR 242.1001(a)(2), *Obligations related to policies and procedures of SCI entities* — https://www.law.cornell.edu/cfr/text/17/242.1001
- CPython documentation, `time` module — `time.sleep()` accuracy and the 3.11 platform changes, `time.perf_counter()` monotonicity — https://docs.python.org/3/library/time.html

## Category

`real-time-architecture` — see top-level `mappings/` directory.
