# Standards for Algo Wheel Execution Quality

The wheel should be treated as a controlled TCA decision, not as an automatic
claim that the lowest historical average is always the best broker.

## Measurement Standards

| Metric | Standard | Engineering implication |
|---|---|---|
| Decision price | Capture before routing using a documented timestamp and price source. | Reject stale or missing benchmarks; retain the benchmark with the execution record. |
| Buy IS | `(fill - decision) / decision * 10000 + fee_bps`. | Positive values are costs; negative values are price improvement. |
| Sell IS | `(decision - fill) / decision * 10000 + fee_bps`. | Use the decision price denominator, not the fill price denominator. |
| Explicit fees | `fees / decision_notional * 10000`. | Normalize fee currency and include commissions, taxes, exchange fees, and documented rebates. |
| Broker score | Notional-weighted average IS. | Prevent many small fills from outweighing a small number of large fills. |
| Ranking | Lowest score first; deterministic tie-break required. | Use broker ID as the reference implementation tie-breaker and retain the raw scores. |
| Canary flow | Every non-leading observed broker receives the configured minimum. | Reject a floor configuration that leaves no residual flow for the leader. |

## Data Quality and Comparability

- Validate broker identity, side, finite prices, positive quantity, and positive
  prices before calculating IS.
- Keep partial fills linked to the parent order and document whether the
  decision notional or executed notional is used for aggregation.
- Segment or control for instrument, order size, urgency, venue, spread,
  volatility, and market regime before using broker scores operationally.
- Track sample count and notional coverage; a low-sample broker should not be
  promoted solely because of one favorable fill.
- Retain raw observations and the exact fee, FX, benchmark, and filtering
  configuration used to produce the ranking.

## Allocation Policy

The reference implementation defaults to a 10% minimum for every non-leading
broker and assigns the residual to the leader. With three brokers this produces
80% / 10% / 10%; with one broker it produces 100%. This is a canary policy, not
a universal optimal allocation. Desks may use another approved policy, but it
must preserve a valid probability distribution, define tie handling, and retain
an auditable rationale.

## Best-Execution Context

Broker-wheel output is one input to a broader best-execution framework. Review
applicable obligations and reporting requirements, including MiFID II Article
27 and RTS 28 where relevant, FINRA Rule 5310 for U.S. broker-dealer activity,
and the venue or client mandate. These rules do not make a historical IS average
sufficient by itself; governance, order handling, costs, execution likelihood,
and periodic review remain separate controls.

## Category
`execution-algorithms`
