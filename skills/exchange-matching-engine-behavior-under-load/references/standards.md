# Standards for Exchange Matching Engine Behavior Under Load

## Scope note

Nothing on this page is a regulatory requirement. These are modelling conventions and
venue-documented operational behaviours. The venue figures below are **release- and
segment-specific**: confirm the current values for your session type, market segment and
platform release against the venue's own documentation before wiring a number into code.

## Queueing model

| Quantity | Definition | Source |
|---|---|---|
| Utilisation | $\rho = \lambda / \mu$, where $\mu$ is the engine's sustainable service rate and $\lambda$ the arrival rate. Dimensionless. | Standard single-server queueing notation |
| M/M/1 sojourn time | $W = \dfrac{1/\mu}{1-\rho}$ — total time in system (queueing **plus** service). Valid only for $\rho < 1$. | Kleinrock, *Queueing Systems, Volume 1: Theory* (Wiley, 1975), Ch. 3 |
| M/M/1 queueing delay | $W_q = W - 1/\mu = \dfrac{\rho}{\mu(1-\rho)}$ | As above |
| M/G/1 queueing delay | Pollaczek–Khinchine: $W_q = \dfrac{\lambda E[S^2]}{2(1-\rho)}$ | MIT 6.263, *M/G/1 Queues* (Modiano) |
| M/D/1 queueing delay | Deterministic service ($c_s^2 = 0$) gives $W_q = \dfrac{\rho}{2\mu(1-\rho)}$ — **exactly half** the M/M/1 value at the same $\rho$. Sojourn $W = \dfrac{1}{\mu}\cdot\dfrac{2-\rho}{2(1-\rho)}$. | Pollaczek–Khinchine with $E[S^2] = 1/\mu^2$ |

### Modelling conventions enforced by this skill

| Convention | Rule |
|---|---|
| Service time vs fixed latency | Only the mean **service time** $1/\mu$ may be scaled by $1/(1-\rho)$. Load-independent latency (network transit, serialisation, gateway hops) is **added**. The two inputs must be supplied separately. |
| Input consistency | $\tau_s$ and $C$ describe the same server, so $\tau_s \approx 10^6/C\ \mu\text{s}$. A ratio outside $[0.5, 2]$ is reported and warned on — it almost always means a round-trip latency was passed where a service time belongs. |
| Service distribution | M/M/1 is the **conservative** choice; a sequenced single-threaded matching engine is closer to M/D/1, which halves the queueing term. Choose one per venue and hold it fixed. M/G/1 with a measured $E[S^2]$ is better still where the service-time distribution is known — this module does not implement it. |
| Saturation | $\rho \ge 1$ has no steady state. Any finite figure is a censored lower bound and must be flagged as such, never reported as an estimate. |
| Censoring | $\rho$ is clamped at $0.99$ before $1/(1-\rho)$ is evaluated, so the reported latency is a lower bound for **any** $\rho$ above that cap, saturated or not. Both conditions are flagged separately. |
| Numeric range | Finite inputs can still overflow through the $1/(1-\rho)$ amplification. An overflowed result must raise, not be returned as an infinite latency. |
| Threshold semantics | Directive thresholds are non-strict lower bounds evaluated on the **exact** $\rho$. Rounding $\rho$ before the comparison shifts the boundary and can halt quoting early. |
| Arrival process | Poisson arrivals are assumed and are **empirically wrong** for order flow, which is clustered and self-exciting. The model is therefore optimistic during bursts; compensate by feeding a peak-window $\lambda$, not a session mean. |

### Directive bands

| Band | Condition | Directive | Risk level |
|---|---|---|---|
| Healthy | $\rho < 0.50$ | `NORMAL_OPERATIONS` | `LOW` |
| Moderate | $0.50 \le \rho < 0.85$ | `WIDEN_PASSIVE_SPREADS` | `MODERATE` |
| High | $\rho \ge 0.85$ | `PAUSE_PASSIVE_QUOTING` | `HIGH_SNIPING_RISK` |

These bands are this skill's own operating convention, not a venue or regulatory
requirement. They are constructor parameters precisely because the right values depend on
the venue, the instrument and the strategy's tolerance for adverse selection — calibrate
them against your own fill and mark-out data rather than adopting them as given.

## What venues actually do above a throttle

The queueing model assumes an unbounded buffer. **No major venue behaves that way at the
session boundary** — each sheds load by rejecting, then disconnects. This is the single
most important limitation of the model above.

| Venue | Documented behaviour |
|---|---|
| CME Globex (iLink) | Messaging controls measure transactions per second at the **iLink session** level, counted over a pre-defined interval that resets when no violation occurred. Exceeding a Reject threshold causes subsequent messages to be rejected via a Business Level Reject (`35=j`) until the rate falls back below it; exceeding the larger **Terminate** threshold terminates the iLink session. Thresholds differ between Convenience Gateway sessions (measured across all market segments) and MSGW sessions (measured per market segment). |
| Eurex T7 (ETI) | Sessions carry a transaction limit (throttle) counted over a **sliding window**. Breaching it rejects the transaction; a run of consecutive throttle rejects disconnects the session — Eurex documents a disconnect limit of 450 consecutive rejects for 150 msg/s sessions and 150 for 50 msg/s sessions, with all ETI sessions dropped to 30 transactions/sec when the disaster-recovery facility is in use. T7 additionally has a **slow partition** state in which participants receive "TRANSACTION REJECTED DUE TO SLOW PARTITION"; applications are expected to handle these rejects. |
| Nasdaq INET / OUCH | Throttling limits are applied per order entry port to protect exchange and member systems. Nasdaq Nordic/Baltic lowered the per-port limit to 5,000 messages/second (from 20,000) in 2020, with a 50 updates/second/connection limit for warrant and certificate order books; messages above the threshold may be rejected. |

**Implication for this skill.** Model output is meaningful only *below* the session
throttle. Once you are being throttled, the failure mode is reject handling and session
loss — see `matching-engine-throttle-and-message-gapping-detection` and
`order-to-trade-ratio-fee-penalty-avoidance`. Also note the throttle applies to *your*
session, whereas $\rho$ describes the *aggregate* load on the partition: you can be well
inside your own limit while the engine is congested by everyone else.

## Why congestion is an adverse-selection problem

Latency arbitrage is the extraction of rents from *symmetrically observable* public
information by whoever reacts first — structurally distinct from the asymmetric private
information of classical microstructure models (Budish, Cramton & Shim, QJE 2015). Engine
congestion widens the window in which a resting quote is stale and a cancel is in flight,
so the same message-rate burst that signals the price move also delays the cancel that
would have avoided being picked off. That asymmetry — delay is symmetric, the payoff is
not — is why the response is to stop quoting rather than to quote faster.

## Sources

- CME Group Client Systems Wiki — *Messaging Controls*: https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457317540
- CME Group Client Systems Wiki — *CME Globex Market Segment Gateway Safeguards*: https://cmegroupclientsite.atlassian.net/wiki/display/EPICSANDBOX/CME+Globex+Market+Segment+Gateway+Safeguards
- Eurex — *T7 Enhanced Trading Interface (ETI) Manual* (transaction limits, throttle and disconnect limits): https://www.eurex.com/resource/blob/5032534/b34946612a479b4b9018590fc584a49a/data/T7_R.14.1_Enhanced_Trading_Interface_-_Manual_Version_2.pdf
- Nasdaq — *INET Nordic: Changes of throttling limits for FIX and OUCH order entry ports* (23/20): https://view.news.eu.nasdaq.com/view?id=b3b2b4e44dd9245f5d087631d4f28e3a1&lang=en
- Nasdaq — *OUCH 5.0 Order Entry Specification*: https://nasdaqtrader.com/content/technicalsupport/specifications/TradingProducts/Ouch5.0.pdf
- L. Kleinrock, *Queueing Systems, Volume 1: Theory*, Wiley, 1975 (M/M/1 results)
- E. Modiano, MIT 6.263 — *M/G/1 Queues* (Pollaczek–Khinchine): https://web.mit.edu/modiano/www/6.263/lec8.pdf
- E. Budish, P. Cramton & J. Shim, "The High-Frequency Trading Arms Race: Frequent Batch Auctions as a Market Design Response", *Quarterly Journal of Economics* 130(4), Nov 2015, pp. 1547–1621: https://academic.oup.com/qje/article/130/4/1547/1916146
- Order-flow clustering / self-excitation (why Poisson arrivals understate burst delay): P. Wu et al., "Queue-reactive Hawkes models for the order flow", arXiv:1901.08938: https://arxiv.org/abs/1901.08938
