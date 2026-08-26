# Workflows for Leverage Limit Enforcement

Full procedure behind `LeverageLimitEnforcerEngine.audit_proposed_order()`.
Notation: $E_i$ is the signed underlying-equivalent exposure of instrument $i$
in USD, $Q$ is portfolio equity.

## 1. Validate every input before any arithmetic

A leverage gate that coerces bad input produces a confident wrong number, which
is worse than an exception.

- `portfolio_equity_usd` must be a finite number strictly greater than zero. A
  `NaN` equity passes a naive `<= 0.0` guard and then propagates through every
  division, yielding a report of `nan` ratios in which every comparison is
  `False` — a rejection for the wrong reason and an unauditable record.
- `side` must be exactly `BUY` or `SELL` after strip/upper. Reject aliases;
  do not map the unknown case to either direction.
- `notional_usd` and `order_notional_usd` must be finite and non-negative.
  Direction lives in `side` alone — a negative notional double-encodes it and
  desynchronises the gross measure (which takes an absolute value) from the net
  measure (which does not).
- `exposure_delta` must be finite. It may be negative: that is how a long put
  becomes short underlying exposure.
- `symbol` and `asset_class` must be non-empty; both are normalised to stripped
  upper case. Symbol normalisation is deliberate — failing to recognise
  `btc-perp` and `BTC-PERP` as one instrument makes a closing order look like a
  new opposing leg.

## 2. Build the signed exposure map

For each position, $E_i = s_i \times n_i \times \delta_i$ where $s_i = +1$ for
`BUY` and $-1$ for `SELL`.

Key the map by normalised symbol and **sum** rows sharing a key. A book holds
one position per instrument; two rows for `AAPL` are one net position, not two.
If the same symbol arrives under two different asset classes, raise — netting
and per-class caps cannot both be resolved from contradictory metadata.

## 3. Project the post-fill book

Copy the map and apply the order to its symbol's key:

$$E_{\text{sym}}^{\text{proj}} = E_{\text{sym}}^{\text{cur}} + s_o \times n_o \times \delta_o$$

This single line is what separates a working gate from one that vetoes
de-risking. Appending the order as a fresh row makes a $\$100\text{k}$ sell
against a $\$300\text{k}$ long read as $\$400\text{k}$ of gross exposure instead
of $\$200\text{k}$.

Then aggregate:

$$\text{Gross} = \sum_i |E_i| \qquad \text{Net} = \left|\sum_i E_i\right| \qquad \text{Class}(c) = \sum_{i \in c} |E_i|$$

Gross does **not** net across different symbols. That offset assumes the
correlation hedge holds, and correlations converge toward one in exactly the
stress event the cap exists for.

Divide each by $Q$ for the leverage ratios.

## 4. Resolve the asset-class cap

Look the order's asset class up in `asset_class_limits`. On a miss, fall back to
`default_asset_class_limit` **only if it was explicitly configured**; log that
the fallback was applied. An instrument type nobody configured must not trade
against a limit nobody chose.

If no fallback was configured, treat the class gate as **failed**, not absent:
`is_asset_class_limit_passed=False`. Do not short-circuit to a rejection here.
The order still goes through step 6, so an order that reduces exposure in that
class is approved as risk-reducing while an opening order returns
`REJECTED_UNKNOWN_ASSET_CLASS`. Short-circuiting would trap any position
already on the book behind a configuration gap — the same failure mode as
vetoing de-risking, arrived at from a different direction.

## 5. Compare on unrounded ratios

$$L_{\text{projected}} \le \text{limit} \times (1 + 10^{-9})$$

The tolerance exists solely so a ratio sitting exactly on a cap is not rejected
by binary float representation. Rounding the ratio to 2 dp *before* the
comparison — `round(L, 2) <= 3.0` — admits any breach below $0.005\times$ NAV
and then records the book as sitting exactly on the cap. Round for the report;
never for the gate.

## 6. Classify the order

Let $\Delta_m$ denote each of the three measures (gross, net, order's class).

- **Approve** when all three are within their caps.
- **Approve as risk-reducing** when the order raises none of the three *and*
  strictly lowers every measure currently in breach. Status
  `APPROVED_RISK_REDUCING_WHILE_OVER_LIMIT`, `is_risk_reducing=True`. A book can
  breach a cap without any order being placed — a mark moving, equity dropping,
  or a limit being tightened. If the gate then blocks every reduction, the desk
  is trapped in the breach it needs to cure.
- The "strictly lowers" condition is load-bearing. Reversing a $\$400\text{k}$
  long into a $\$400\text{k}$ short leaves gross, net, and class leverage
  unchanged. Nothing was remediated and an $\$800\text{k}$ order was placed, so
  it is vetoed.
- **Veto** otherwise, in precedence order gross $\to$ net $\to$ asset class, so
  the reported reason is the broadest breached constraint.

## 7. Emit the report

`LeverageEnforcementReport` carries current and projected ratios at full
precision, the three pass flags, the order's class and the cap applied to it,
every class's projected leverage, `is_risk_reducing`, and a human-readable audit
note. `report.is_approved` is the single field a router should branch on.

Classes the order does not touch are unchanged by it and therefore do not veto
it — but a pre-existing breach elsewhere is logged at WARNING rather than left
silent.

## 8. Operate it safely

- **Serialize check-then-place.** The engine is stateless and safe to share
  across threads, but it evaluates one order against one snapshot. Two orders
  checked concurrently can each pass and jointly breach. Hold the lock across
  the check *and* the submission.
- **Re-check after partial fills.** An approval is for the full order notional.
  A partially filled parent leaves the book somewhere between the current and
  projected states; re-evaluate before working the remainder.
- **Refresh marks first.** The ratios are exactly as fresh as the notionals
  supplied. Stale marks during a gap produce a confidently wrong approval.
- **Log every decision, including approvals.** Rule 15c3-5 controls are
  examined on evidence that they ran, not on the assertion that they exist.
