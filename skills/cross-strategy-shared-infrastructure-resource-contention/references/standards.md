# Standards for Shared Infrastructure Resource Contention

## Internal engineering defaults (not regulatory figures)

No regulator publishes a numeric host-utilisation trigger. The table below
records this module's defaults and the reasoning behind them; calibrate them
against your own capacity and stress tests before relying on them.

| Parameter | Default | Rationale |
|---|---|---|
| `elevated_threshold_pct` | 75% | Watch level: hold back new background work, do not preempt. |
| `critical_threshold_pct` | 85% | Preemption level, applied to the single most-loaded resource (max, never an average). |
| `resume_threshold_pct` | 75% (= elevated) | De-escalation is asymmetric so the control does not flap across the critical line. |
| `resume_clear_samples` | 3 | Consecutive clear samples before release. Multiply by the telemetry interval for the real dwell time. |
| `medium_priority_throttle_factor` | 0.5 | Message-rate cap left to `MEDIUM_ARB` under CRITICAL, expressed against a declared baseline where one is supplied. |
| Telemetry normalisation | CPU/RAM in `[0, 100]` | Un-normalised multi-core aggregates are rejected rather than scored. |

## Verified external claims

| Claim | Source |
|---|---|
| `taskset` sets CPU affinity, and success "does not guarantee that the specified thread has actually migrated to the indicated CPU(s), but only that the thread will not migrate to a CPU outside the new affinity mask" — i.e. affinity restricts, it does not reserve. Keeping other work off a core additionally requires isolation (cgroup cpusets, `isolcpus`/`nohz_full`) and IRQ affinity. | `taskset(1)` man page — https://man7.org/linux/man-pages/man1/taskset.1.html ; SUSE Labs, "CPU Isolation – A practical example" — https://www.suse.com/c/cpu-isolation-practical-example-part-5/ |
| Venue message-rate throttles are per-session and venue-allocated, not a FIX-protocol constant. On CME iLink 3, exceeding a Reject threshold means "subsequent messages are rejected via a BusinessLevel Reject (tag 35-MsgType=j) message until the messages per second (MPS) rate falls below the threshold"; "Exceeding the larger Terminate threshold will result in a Termination of the offending iLink session." | CME Group Client Systems Wiki, *Messaging Controls* — https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457317540/Messaging+Controls |
| EU: investment firms engaged in algorithmic trading must have pre-trade controls including "maximum messages limits, which prevent sending an excessive number of messages to order books pertaining to the submission, modification or cancellation of an order" (Art. 15(1)(d)), and "repeated automated execution throttles which control the number of times an algorithmic trading strategy has been applied" (Art. 15(3)). | Commission Delegated Regulation (EU) 2017/589 (RTS 6), Article 15 — https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0589 |
| EU: emergency order cancellation is a distinct control from load shedding — a firm "shall be able to cancel immediately, as an emergency measure, any or all of its unexecuted orders submitted to any or all trading venues." | RTS 6, Article 12 (kill functionality) — same source |
| EU: real-time monitoring of algorithmic trading during trading hours, with alerts generated "within five seconds after the relevant event" — a practical upper bound on the telemetry/alerting interval for this loop. | RTS 6, Article 16 — same source |
| EU: annual stress testing must comprise high-messaging-volume and high-trade-volume tests sized on the highest volumes of the previous six months multiplied by two — the basis for sizing `max_fix_gateway_rate_sec` headroom rather than guessing it. | RTS 6, Article 10 — same source |
| US: pre-trade controls must "Prevent the entry of orders that exceed appropriate pre-set credit or capital thresholds" (para. (c)(1)(i)) and "Prevent the entry of erroneous orders, by rejecting orders that exceed appropriate price or size parameters" (para. (c)(1)(ii)), and those controls "shall be under the direct and exclusive control of the broker or dealer" (para. (d)). Load shedding must therefore never degrade the pre-trade checks themselves. | 17 CFR § 240.15c3-5 (Risk management controls for brokers or dealers with market access) — https://www.law.cornell.edu/cfr/text/17/240.15c3-5 |

## Scope and applicability notes

- RTS 6 applies to investment firms engaged in algorithmic trading in the EU;
  the UK retains an assimilated version through the FCA. SEC Rule 15c3-5 applies
  to US broker-dealers with market access. Neither prescribes host CPU or memory
  thresholds — they are cited here for message-rate limits, kill functionality,
  monitoring cadence, and the exclusivity of risk controls, not for the numbers
  in the first table.
- The CME iLink 3 thresholds are exchange-specific. Every venue and broker
  allocates its own per-session limits; read your own session's allocation
  rather than porting CME's.
- Sources above were verified in August 2026. Venue throttle documentation in
  particular changes without notice.
