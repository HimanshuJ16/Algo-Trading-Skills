# Standards for Correlation-Aware Exposure Limits

No securities regulator, exchange, or standards body prescribes a correlation
threshold, a clustering method, or a cluster exposure cap. Every number below is a
**configurable default of this module**, chosen to be conservative, not a figure any
external authority mandates. Rule 15c3-5 and its non-US analogues require that pre-set
credit and capital thresholds exist and are enforced pre-trade; they are silent on how
those thresholds are derived. Set yours from your own mandate and your own return
history.

## Module defaults — `CorrelationExposureManager.__init__`

These are the constructor's actual signature values. Read them as starting points to
calibrate, and note the units: the caps are **absolute notional**, not percentages of
NAV.

| Parameter | Default | What it actually does |
| :--- | :--- | :--- |
| `correlation_threshold` | `0.7` | Pairwise Pearson correlation at or above which two symbols join the same cluster. Validated to lie in $[-1, 1]$. Clustering is transitive: A-B and B-C at threshold place A, B and C in one cluster even when A-C is below it. |
| `max_cluster_notional` | `1_000_000.0` | Cap on **post-trade, delta-adjusted gross** exposure per cluster. Must be positive and finite. |
| `max_portfolio_notional` | `3_000_000.0` | Cap on **post-trade raw gross** notional across the book. Deliberately *not* delta-adjusted — it is a capital limit, where the cluster cap is a risk-concentration limit. |
| `sector_mapping` | `None` | Symbol → sector label. Symbols sharing a non-`None` label are forced into one cluster regardless of measured correlation. |
| `max_matrix_age_days` | `7.0` | Age beyond which the correlation matrix counts as stale. Must be positive. |
| `stale_matrix_policy` | `"warn"` | `"warn"` logs and proceeds; `"block"` refuses the check. Any other value raises at construction. |

There is no lookback-window parameter. The caller supplies the price series, and the
module correlates the most recent overlapping returns; a shorter series is correlated
over its own length, with a warning. Window length is therefore the caller's decision
and its consequences are the caller's to manage — see the calibration note below.

## Why the caps are gross, and why that is not the broker's number

Cluster and portfolio exposure are sums of **absolute** notionals. Netting a long
against a short inside a correlated cluster assumes the hedge holds precisely in the
conditions the cap exists to survive, and correlations converge toward 1 under stress.
Gross is the conservative basis.

The direct consequence: these numbers **will not match broker margin**, which does
grant offsets, and they are not intended to. Do not drive collateral or margin
decisions from them — see `multi-leg-strategy-margin-optimization` and
`options-margin-span-calculation-global`.

## Calibrating the threshold

$0.70$ is a policy dial, not an estimate of anything. Two properties should drive the
number you pick instead:

1. **Transitive chaining.** Because clustering takes connected components, a chain of
   pairwise-0.70 edges merges into one pocket even where the endpoints are nearly
   uncorrelated. Lowering the threshold does not shrink caps smoothly — it can collapse
   the whole book into a single cluster at a tipping point. Plot cluster count and
   largest-cluster size against candidate thresholds on your own universe before
   choosing, rather than reasoning about the number in the abstract.
2. **Estimation noise.** A sample correlation is an estimate. Under Fisher's
   $z$-transform, $z = \operatorname{artanh}(r)$ is approximately normal with standard
   error $1/\sqrt{n-3}$ for $n$ paired observations, which for $r$ near $0.7$ and
   $n = 60$ puts a 95% interval at roughly $0.54$ to $0.81$. A threshold applied to a
   60-day window is therefore a coin-flip for any true correlation near it — which is
   why the skill's guidance is to widen the lookback or fall back to sector-only
   clustering rather than trust a marginal edge.

A defensible calibration is a percentile of your own rolling pairwise correlation
distribution (for example, cluster the top decile of pairs) reviewed on a fixed cadence,
so the threshold tracks the universe instead of being frozen at a round number.

## Fail-closed behaviour

| Condition | Required behaviour | Implemented as |
| :--- | :--- | :--- |
| No matrix ever built | Evaluation must not approve anything | `CorrelationMatrixUnavailableError` raised by `_require_usable_matrix()` |
| Matrix older than `max_matrix_age_days` | Warn or refuse, per policy | `check_staleness()`, then `stale_matrix_policy` |
| Non-positive, non-finite or NaN prices | Reject the input | `ValueError` from `update_correlation_matrix()` |
| Non-finite delta weight | Reject the evaluation | `ValueError` from `evaluate_proposed_position()` |
| Cap breached | Veto, and report the size that would fit | `RiskCheckResult(approved=False, allowed_notional=...)` |

The last row is the one to preserve when adapting the module: a veto that reports only
"denied" forces the caller to bisect for a workable size, and a caller that bisects
against a live risk check is issuing repeated pre-trade requests to find a way through
a limit. Return the headroom instead.

## Regulatory touchpoint

- **SEC Rule 15c3-5 (17 CFR 240.15c3-5)**, the Market Access Rule, requires a
  broker-dealer with market access to maintain risk management controls reasonably
  designed to prevent the entry of orders that exceed pre-set credit or capital
  thresholds, applied **on a pre-order-entry basis**. The obligation sits with the
  sponsoring broker-dealer rather than directly with a buy-side fund; a firm-side
  cluster cap is the buy-side implementation of the same idea, not a discharge of the
  broker's duty. The rule prescribes no threshold values.
  <https://www.sec.gov/files/rules/final/2010/34-63241-secg.htm>
- **EU MiFID II RTS 6, Article 15** imposes the comparable pre-trade limit obligation
  on investment firms engaged in algorithmic trading, likewise without prescribing
  values.

Concentration limits in the Basel large-exposures framework are sometimes cited in this
context. They govern bank exposures to counterparties, not a fund's exposure to
correlated instruments, and do not transfer — cite them only if your entity is actually
in scope.
