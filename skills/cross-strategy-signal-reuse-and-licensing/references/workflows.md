# Workflows for Cross-Strategy Signal Reuse and Licensing

## 1. Signal Registration

- Record `owner_entity`, base licence fee, PnL share fraction (in [0, 1]), and max AUM capacity.
- `register_signal()` refuses to overwrite an existing `signal_id`. To re-price, pass
  `replace=True`; the engine logs the AUM already subscribed under the old terms so
  those grants can be re-reviewed rather than quietly migrated.
- Capacity should be derived from a capacity study (turnover, participation rate,
  holding-period overlap), not chosen to fit the current book. AUM is the unit this
  engine enforces, but it is a proxy — see `strategy-capacity-estimation-before-scaling-capital`.

## 2. Entitlement Audit

- Construct a `StrategySubscription`. Invalid input (blank ids, NaN, infinite, or
  negative AUM, non-bool `is_active`) raises at construction. This is deliberate:
  `nan > cap` evaluates False, so an unvalidated NaN would be **granted** and would
  then poison every later capacity sum.
- `request_subscription()` checks $\sum \text{Active Subscribed AUM} + \text{New AUM} \le \text{Max Capacity}$,
  using a relative tolerance of 1e-9 so a projection landing exactly on the cap is
  not denied by floating-point accumulation.
- Denials return `EntitlementCheckResult(is_entitled=False)` and are **not** stored,
  so a rejected pod never consumes headroom.
- A duplicate `subscription_id` raises `DuplicateRegistrationError`. Revoke the
  existing grant first; ids are unique per grant, not per pod.
- An `is_active=False` request is denied rather than recorded — an entitlement row
  that is already revoked would make the audit trail disagree with reality.

## 3. Revocation

- `revoke_subscription(subscription_id)` sets `is_active=False`, releasing the AUM
  back to capacity while retaining the record for audit. It is idempotent, and
  raises `UnknownSubscriptionError` for an unknown id.
- Revoke on pod decommissioning, allocation change (revoke then re-subscribe with a
  new id and the new AUM), or licence termination.

## 4. Fee Attribution

- $\text{Fee} = \text{Base Fee} + \text{PnL Share Pct} \times \max\left(0, \text{Realized PnL} - \text{Loss Carryforward}\right)$.
- Pass `loss_carryforward_usd` as a positive number representing unrecouped prior
  losses (a high-water mark). Roll `remaining_loss_carryforward_usd` from the report
  into the next period. Omitting it charges the licensee on the same dollars twice
  across a drawdown-and-recovery cycle — a term no unrelated party would accept.
- Billing a revoked subscription, or one whose signal is no longer cataloged, raises.
- Pass `benchmarking_evidence_ref` (comparability study or intercompany agreement id).
  Without it the engine logs a warning and sets `arm_length_documented=False`.

## 5. Audit Logging

- `generate_audit_report(signal_id)` returns capacity utilisation, remaining headroom,
  active and revoked subscription ids, consumer entities, and the `pricing_basis` note.
- Issue the licensing certificate and fee schedule to the consumer pod, and retain the
  report alongside the intercompany agreement for the local file (OECD TPG 2022 Ch. V).
- The engine records internal entitlement only. External vendor/venue redistribution
  and derived-data permissions are tracked separately — see `references/standards.md`.
