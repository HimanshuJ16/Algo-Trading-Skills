# Pre-Flight Checklist

## Input conventions (each one silently changes the routing if wrong)

- [ ] Is `realized_slippage_bps` a **positive cost magnitude in basis points**
      (`15.0`, not `-15.0`)? A signed convention makes the ratio negative, so it never
      exceeds the limit and Node 2 is silently disarmed.
- [ ] Are `realized_slippage_bps` and `expected_alpha_bps` measured over the **same
      horizon and the same unit of activity** — both per round trip, or both per unit
      time? Per-trade slippage against annualized alpha understates the ratio by
      roughly the number of trades per year.
- [ ] Is `expected_alpha_bps` **strictly positive**? Zero or negative expected alpha is
      a Node 1 hypothesis question, not a Node 2 execution question.
- [ ] Do `live_sharpe`, `backtest_sharpe` and `peer_benchmark_sharpe` share an
      **annualization convention and return frequency**?
- [ ] Does `live_observation_count` count **live** return observations, not backtest
      observations?
- [ ] Are non-finite values (`NaN`/`Inf`) and stringified booleans (`"False"`) rejected
      upstream rather than passed through? `"False"` is truthy and would clear Node 1.

## The two judgment inputs

- [ ] Is `is_alpha_hypothesis_valid` a **research judgment about the economic
      mechanism** — has the inefficiency been arbitraged away, has the market structure
      or fee schedule that created it changed — and **not** a re-reading of live P&L?
- [ ] Is that judgment recorded with its author and date, ideally declared before the
      drawdown rather than after it?
- [ ] If the judgment is "invalid" while the data feed is unhealthy or the peer group is
      also impaired, has the `warnings` caveat been read before acting? Decommissioning
      is irreversible; a feed repair and a regime shift are not.
- [ ] Is `is_data_feed_healthy` sourced from actual data-quality monitoring rather than
      defaulted to `True`?

## Peer benchmark and thresholds

- [ ] Is `peer_benchmark_sharpe` a **defensible cohort** for this strategy? Benchmarking
      a market-neutral book against a long-only index manufactures a Node 4 verdict
      every time the index rallies.
- [ ] Has the benchmark identity been recorded with the report? Swapping it resets the
      comparison.
- [ ] Are `min_healthy_sharpe` and `min_peer_sharpe` calibrated to this firm's mandate
      and defensible to a committee, rather than accepted as house defaults? Neither is
      an external standard.
- [ ] Is the asymmetry between them understood — a strategy at 0.95 against peers at
      0.55 is routed to recalibration *while leading its cohort*?

## Statistical honesty

- [ ] Is `min_live_observations` configured? Without it, no sample-size gate exists and
      the tree will route a three-week-old strategy to decommissioning.
- [ ] Has `sharpe_evidence_conclusive` been checked before describing a strategy as
      "underperforming" in a memo? At 60 daily observations the standard error near
      SR $=1.0$ is $\approx 2.05$, larger than the threshold itself, so it will
      usually be `False`.
- [ ] Has `sharpe_gap_vs_peer` been read on every Node 4 verdict, not just the action?
- [ ] Is a proper Sharpe-difference test
      (`strategy-performance-decay-detection-vs-market-wide-decay`) run before any
      decommissioning decision, rather than relying on two threshold comparisons?

## Reading the report

- [ ] Is `warnings` empty? If not, has every entry been read? An empty tuple means no
      caveats applied.
- [ ] Does `triage_path` show the cleared nodes as well as the decisive one? A cleared
      node in the record is the evidence that the alternative was tested.
- [ ] Has the sequence of diagnoses for this strategy been reviewed, not just the latest
      one? Repeated Node 4 verdicts across quarters are better evidence of alpha decay
      than any single evaluation.

## Downstream wiring and governance

- [ ] Is something other than this engine actually executing the recommendation? Every
      outcome is a field on a dataclass — nothing here cancels orders, liquidates
      positions, halts a strategy, or changes an allocation.
- [ ] Is `MANDATORY_STRATEGY_DECOMMISSION` wired to
      `strategy-decommissioning-and-position-unwind-procedure`, with the independent
      halt path in `kill-switch-and-drawdown-circuit-breakers` left untouched?
- [ ] Is the recalibration performed with walk-forward re-optimization and out-of-sample
      validation, then **retested** before redeployment?
- [ ] For EU/UK firms: has the person designated by senior management authorised the
      change before it goes live (MiFID II RTS 6 Art. 5), and is it recorded with when,
      who made it, who approved it, and its nature (Art. 5(7))?
- [ ] Is each recalibration recorded and retested **individually**, rather than batched?
      ESMA's February 2026 supervisory briefing (¶30) warns that a series of small
      recalibrations can accumulate into an untested material change.
