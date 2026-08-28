# Standards for Order Book Imbalance Signal Generation

There is no exchange or regulatory specification for order book imbalance — it is
a derived research quantity, not a published field. The definitions below are
therefore sourced to the literature, and the engineering rules to this repository.
Nothing in this skill imposes a regulatory obligation; the one regulatory entry
below is context for what the *input* data can be, not a compliance requirement
on the calculation.

## Definitions

| Quantity | Definition | Range | Source |
|---|---|---|---|
| Signed queue imbalance $I$ | $\dfrac{V_{\text{bid}} - V_{\text{ask}}}{V_{\text{bid}} + V_{\text{ask}}}$ | $[-1, +1]$ | Convention used across this repository; see `order-book-depth-processing-l2-l3` and `order-book-microstructure-signal-research`, which use the identical form. |
| Unsigned imbalance $I'$ | $\dfrac{V_{\text{bid}}}{V_{\text{bid}} + V_{\text{ask}}}$, with $I = 2I' - 1$ | $[0, 1]$ | Stoikov's convention: "the imbalance $I$ is calculated as $I = Q_b/(Q_b+Q_a)$" (Aleksander & Granmo et al., *High resolution microprice estimates from limit orderbook data*, arXiv:2411.13594, §2). |
| Weighted mid-price $W$ | $W = I' P_{\text{ask}} + (1 - I') P_{\text{bid}} = \dfrac{V_{\text{bid}} P_{\text{ask}} + V_{\text{ask}} P_{\text{bid}}}{V_{\text{bid}} + V_{\text{ask}}}$ | $[P_{\text{bid}}, P_{\text{ask}}]$ | Same source, stated as "$W = I \cdot P_a + (1-I) \cdot P_b$". |
| Mid-price $M$ | $\dfrac{P_{\text{bid}} + P_{\text{ask}}}{2}$ | — | Standard. |
| Spread $s$ | $P_{\text{ask}} - P_{\text{bid}}$ | $\ge 0$ on an uncrossed book | Standard. |
| Stoikov micro-price | $P_{\text{micro}} = M + g(I, S)$ — **not computed by this skill** | — | Stoikov, *The Micro-Price: A High Frequency Estimator of Future Prices* (SSRN 2970694). |

### The one identity that matters

$$W - M = \frac{(V_{\text{bid}} - V_{\text{ask}})(P_{\text{ask}} - P_{\text{bid}})}{2(V_{\text{bid}} + V_{\text{ask}})} = \frac{I \cdot s}{2}$$

Weighted-mid divergence from the mid is therefore **the imbalance times the
spread, exactly** — it is not an independent signal and must not be scored as
one. The engine uses the identity as a unit-test invariant instead.

### Naming

What this skill computes is the **weighted mid-price**. Industry shorthand often
calls it "the micro-price"; Stoikov's micro-price is a different object, a
calibrated estimator of the future mid conditional on imbalance and spread, and
is a martingale by construction. The weighted mid is not, and inherits none of
the empirical results reported for the micro-price.

## Evidence base

| Claim | Status | Source |
|---|---|---|
| Bid/ask queue imbalance has a statistically significant relationship with the direction of the *next* mid-price move | Supported, on 10 liquid Nasdaq stocks | Gould & Bonart, *Queue Imbalance as a One-Tick-Ahead Price Predictor in a Limit Order Book*, arXiv:1512.03492 (2015) |
| That relationship is stronger for large-tick than small-tick instruments | Supported — "a considerable improvement … for large-tick stocks, and … a moderate improvement … for small-tick stocks" | Gould & Bonart, ibid., abstract |
| Depth beyond the touch adds information at longer horizons | Supported, meso-scale timeframes | Order-flow / LOB-resiliency literature, e.g. arXiv:1708.02715 |
| Depth beyond the touch reduces noise in a *tick-horizon* signal | **Not supported.** Aggregating levels changes the horizon the signal addresses; it is not a filter on the horizon you had. | — |
| Any specific threshold value (0.60 or otherwise) is broadly applicable | **Not supported.** No source establishes a transferable trigger level. Treat `0.60` as a placeholder default requiring per-instrument calibration. | — |

## Input-integrity rules enforced by the engine

| Condition | Rejection kind | Why it cannot be measured |
|---|---|---|
| Volume non-finite (`NaN`, `inf`), negative, or non-numeric | `INVALID_VOLUME` | `NaN` defeats a `total <= 0` guard and yields a `NaN` signal classified `NEUTRAL`; a negative size pushes $\|I\|$ outside $[-1, 1]$ (bid $-100$ vs ask $200$ → $I = -3.0$). |
| Price non-finite, zero or negative | `INVALID_PRICE` | A zero price produces $W = 0$ and $M = 0$ — a plausible-looking price handed to an execution worker. |
| $P_{\text{bid}} > P_{\text{ask}}$, or $=$ without `allow_locked_book` | `CROSSED_OR_LOCKED_BOOK` | The two sides are from different moments; the imbalance is well defined and meaningless. |
| `timestamp_ns` not a non-negative integer (`NaN`, float, string, `None`) | `INVALID_TIMESTAMP` | A `NaN` passes the ordering comparison, is stored, and disables the regression check for the next update as well. |
| `timestamp_ns` below the last accepted value for that symbol | `TIMESTAMP_REGRESSION` | The update is superseded. Comparison is within one symbol's feed clock domain only. |
| Aggregated volume zero over the configured levels | `EMPTY_BOOK` | Absence of data, not a balanced book. |
| Fewer depth levels supplied than `depth_levels` | `INSUFFICIENT_DEPTH` | A silent downgrade changes the signal's definition mid-stream. |
| Depth ladder repeating the touch, non-monotonic, malformed, or not a concrete `list`/`tuple` | `MALFORMED_DEPTH` | Including level 1 in `bid_depth`/`ask_depth` double-counts the best queue; a generator is consumed by the scan and then fails the length check with an unhandled `TypeError`. |

Rejected updates return `signal_type = UNRELIABLE` with every numeric field
`None` — never `0.0`, never `NaN`. A `None` raises when a consumer does
arithmetic on it; a zero silently prices an order.

## Regulatory context (informational)

| Item | Jurisdiction | Status |
|---|---|---|
| Spoofing — "bidding or offering with the intent to cancel the bid or offer before execution" — is unlawful on registered entities under CEA §4c(a)(5)(C), 7 U.S.C. §6c(a)(5)(C), added by Dodd-Frank §747. Liability requires proof of intent. | US futures/swaps (CFTC-registered DCMs and SEFs) | In force |

This is context for the input, not an obligation on this skill: displayed depth
is manipulable, a prohibition is enforced after the fact, and a fill taken
against layered size is not undone by it. Own-conduct surveillance belongs to
`wash-trade-and-spoofing-self-detection`.

## Out of scope

Book construction and maintenance (`order-book-depth-processing-l2-l3`),
snapshot/delta resynchronisation
(`market-data-snapshot-plus-delta-reconciliation`), signal calibration and
Information Coefficient evaluation (`order-book-microstructure-signal-research`),
wall-clock staleness (`clock-skew-correction-for-tick-timestamps`), and
tick-to-trade measurement (`tick-to-trade-latency-measurement`).

## Category

`real-time-architecture` — see top-level `mappings/` directory.
