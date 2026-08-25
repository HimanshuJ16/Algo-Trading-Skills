# Workflows for Early Exercise Assignment Risk Management

## 1. Input preparation

1. Pull the short option book. For every position resolve `option_type`, `exercise_style`,
   `strike`, `contracts_qty` (positive count), `contract_multiplier`, and `days_to_expiry`.
2. Mark each option at the **bid**, not the mid or last trade. The long holder's alternative
   to exercising is selling, and selling realises the bid; a mid-based extrinsic value
   overstates the buffer protecting the writer.
3. Attach the declared dividend (`upcoming_dividend_usd`, `days_to_ex_div`) from the corporate
   action calendar. Leave `days_to_ex_div` at `+inf` when nothing is scheduled — never zero.
4. Where available, attach `same_strike_put_price` (same strike, same expiry) and set the
   engine's `risk_free_rate`. This is what upgrades the call test from a conservative screen
   to the exact condition.

## 2. Intrinsic / extrinsic decomposition

- $\text{Intrinsic}_{\text{call}} = \max(0, S-K)$, $\text{Intrinsic}_{\text{put}} = \max(0, K-S)$.
- $\text{Extrinsic} = \max(0,\ P - \text{Intrinsic})$.
- If $P < \text{Intrinsic}$: report `quoted_below_parity` and the `QUOTE_BELOW_INTRINSIC` flag.
  Two readings are possible and both matter — the option is genuinely at or through parity
  (exercising beats selling), or the quote is stale/crossed. Re-mark before acting.

## 3. Exercise-style gate

- `EUROPEAN` → no early exercise is possible; return `LOW_RISK` and stop. The remaining
  expiry-day exposure is pin risk, handled by `options-pin-risk-management-at-expiry`.
- Unrecognised `option_type` or `exercise_style` → raise. Do not default. A silent default
  either prices a call as a put or downgrades an American contract to "cannot be assigned".
- Exercise style is not settlement method. OEX is American *and* cash-settled; XEO on the same
  index is European. Screen OEX-style contracts normally.

## 4. Ex-dividend capture audit (short calls)

1. **Relevance**: if the ex-date falls after expiration, the dividend cannot be captured by
   exercising this contract. Exclude it and flag `DIVIDEND_AFTER_EXPIRY_IGNORED`.
2. **Exact test** (put price available):
   $$\text{TV}_{ex} = p_{ex} + K\left(1 - e^{-r\tau}\right),\qquad \text{edge} = D - \text{TV}_{ex}$$
   Exercise is economically favoured for the holder when $\text{edge} > 0$.
3. **Fallback screen** (no put price): $D > \text{Extrinsic}_{\text{cum-div}}$. Conservative by
   construction — parity gives $\text{Extrinsic}_{\text{cum-div}} = \text{TV}_{ex} - PV(D)$, so the
   screen fires whenever $\text{TV}_{ex} < D + PV(D)$, a superset of the exact condition. Record
   which test ran; never present a screen hit as a verdict.
4. **Windowing**:
   - favoured **and** `days_to_ex_div <= ex_div_decision_days` (1.0) → `CRITICAL_ASSIGNMENT_RISK`,
     action `CLOSE_OR_ROLL_SHORT_CALL`, and compute the dividend liability
     $D \times \text{contracts} \times \text{multiplier}$.
   - favoured **and** within `ex_div_warning_days` (3.0) → `ELEVATED_ASSIGNMENT_RISK`, action
     `MONITOR`. This exists so a desk sees the exposure before the day it must act.

## 5. At-parity audit (calls and puts)

- Threshold: $\max(\text{min\_extrinsic\_threshold\_usd},\ \text{min\_extrinsic\_fraction\_of\_strike} \times K)$,
  default $\max(\$0.05,\ 5\text{bp} \times K)$.
- ITM **and** extrinsic at or below the threshold → `HIGH_ASSIGNMENT_RISK`, action
  `CLOSE_OR_ROLL_SHORT_CALL` / `CLOSE_OR_ROLL_SHORT_PUT`.
- This rule is dividend-independent and covers what the ex-dividend rule cannot see:
  carry-driven put exercise, hard-to-borrow call exercise, and any short pinned at parity.
- For short puts the carry edge is reported when `same_strike_call_price` is supplied:
  $\text{edge} = K(1 - e^{-r\tau}) - c - D_{\text{to expiry}}$. Note the sign of the dividend
  term: a dividend before expiry makes put early exercise **less** attractive.

## 6. Severity resolution and dispatch

1. `risk_level` is the most severe rule that fired
   (`CRITICAL` > `HIGH` > `ELEVATED` > `LOW_RISK`); every firing rule appends its own sentence
   to `risk_summary` so the audit trail shows all reasons, not just the winning one.
2. A `CRITICAL` ex-dividend verdict retains `CLOSE_OR_ROLL_SHORT_CALL` even when the parity
   rule also fires.
3. Route close/roll directives to execution **before your clearing member's exercise cutoff on
   the last cum-dividend session** — not the following morning. The 5:30 p.m. ET deadline in
   FINRA Rule 2360(b)(23)(A) applies to *expiring* options; your broker's early-exercise cutoff
   on an ordinary session is its own and is typically earlier.
4. Persist the full `EarlyExerciseAuditReport` per position and per run. `exercise_test_used`,
   `early_exercise_edge_usd`, and `data_quality_flags` are what make a post-assignment
   post-mortem answerable.

## 7. Post-assignment handling (out of scope for the engine)

If assignment occurs anyway, the writer is short stock from the assignment date. Reconcile the
resulting equity position, the dividend payable in lieu, and the margin impact through the
broker position feed — this engine screens, it does not reconcile.
