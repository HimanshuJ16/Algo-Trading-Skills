---
name: cross-strategy-signal-reuse-and-licensing
description: Use when multiple strategy pods or group entities consume the same
  proprietary alpha signal, to gate entitlement against a signal AUM capacity cap,
  attribute an internal licence fee, and produce an auditable licensing record
domain: Signal Governance & Licensing
subdomain: Alpha Marketplace & Transfer Pricing
tags:
- signal-reuse
- signal-licensing
- entitlement
- alpha-marketplace
- transfer-pricing
- fee-attribution
- aum-capacity
brokers_frameworks:
- OECD Transfer Pricing Guidelines 2022
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in multi-strategy platforms, quantitative research hubs, and internal alpha marketplaces where proprietary signals or alt-data features (e.g. NLP Sentiment, Options Flow, Satellite Spend) are shared across multiple sub-strategy pods or legal entities. Reusing signals scales R&D efficiency, but requires strict entitlement access control, capacity tracking ($\sum \text{AUM}_{\text{active}} \le \text{Capacity}_{\text{max}}$), and a recorded internal fee ($\text{Fee} = \text{Base Fee} + \text{PnL Share} \times \max(0, \text{PnL} - \text{Loss Carryforward})$) that a transfer pricing analysis can be attached to.

## When NOT to Use

- **As a substitute for a transfer pricing analysis.** This module applies a schedule a human already negotiated and benchmarked; it does not determine, test, or certify that the schedule is arm's length. `arm_length_documented` reports only whether a benchmarking reference was attached — it is a documentation-presence flag, never a compliance opinion. Comparability analysis under OECD TPG 2022 Chapters I and VI, and the Chapter V master file / local file record, remain a tax adviser's work.
- **To justify the OECD 5% low value-adding mark-up.** A proprietary alpha signal is core business, is R&D output, and is a unique and valuable intangible, so it is excluded from the Chapter VII simplified approach by paras 7.45 and 7.47 — the 5% mark-up of para 7.61 does not apply to it. Do not price a signal licence that way.
- **As the sole capacity control.** AUM is a coarse proxy. Two pods running the same $20M against the same signal at 10x different turnover crowd it very differently; capacity that actually binds is about traded notional, participation rate, and holding-period overlap. Pair with `strategy-capacity-estimation-before-scaling-capital` and `cross-strategy-correlation-monitoring`.
- **As the vendor-licence control for third-party data.** Redistributing a vendor's data, or a value derived from it, to another entity or business unit is governed by that vendor's or venue's own agreement (derived-data and non-display terms differ per venue and are commonly audited). This engine tracks *internal* entitlement only — use `market-data-entitlement-and-licensing-per-venue` and `data-vendor-contractual-usage-restriction-tracking` for the external permission.
- **As a live pre-trade gate.** Entitlement is evaluated at subscription time, not per order. It does not observe what a pod actually trades after the grant.

## Prerequisites

- Registered signal metadata (`signal_id`, `signal_name`, `owner_entity`, `base_license_fee_annual_usd`, `pnl_share_pct` as a fraction in [0, 1], `max_aum_capacity_usd`).
- Strategy pod subscription requests (`subscription_id` unique per grant, `strategy_id`, `signal_id`, `consumer_entity`, `allocated_aum_usd`).
- A negotiated licence schedule with a benchmarking reference (comparability study or intercompany agreement id) to record against each fee calculation.

## Workflow

1. **Signal Catalog & Capacity Registration**: Register licensing parameters and the capacity cap. Re-registering an existing `signal_id` requires `replace=True` — an unnoticed overwrite silently re-prices and re-caps every live subscription, so the engine refuses it by default and logs a warning with the already-subscribed AUM when you do opt in.

2. **Entitlement Access Verification**:
   - Reject structurally invalid input at the boundary rather than absorbing it. NaN is the case that matters: `nan > cap` is False, so an unchecked NaN AUM would be *granted* and would then make every later capacity sum NaN, permanently disabling the only control that exists. Negative AUM would likewise manufacture headroom. Both raise.
   - Verify total ACTIVE subscribed AUM: $\sum \text{AUM}_{\text{sub}} + \text{AUM}_{\text{new}} \le \text{Max Capacity}$, compared with a small relative tolerance so a projection landing exactly on the cap is not denied by float accumulation.
   - A denial is **not** recorded, so a rejected pod never consumes headroom. A duplicate `subscription_id` raises instead of overwriting — silently replacing a record erases one pod's entitlement history while its AUM is still counted.
   - Revoke with `revoke_subscription()` to release capacity. The record is retained with `is_active=False` so the entitlement history stays auditable, and revocation is idempotent.

3. **Internal Fee Attribution**:
   - $\text{Fee}_{\text{total}} = \text{Base Fee} + \text{PnL Share Pct} \times \max\left(0, \text{PnL} - \text{Loss Carryforward}\right)$.
   - Carry unrecouped losses forward (a high-water mark). Without it a pod that loses \$10M then makes \$1M pays a share on the full \$1M — a term no unrelated licensee would accept, and exactly the kind of divergence from third-party behaviour a transfer pricing review targets. The report returns `remaining_loss_carryforward_usd` to roll into the next period.
   - Billing a revoked subscription raises. So does a fee calculation whose underlying signal is no longer cataloged — the terms are unknown, so do not guess them.
   - Attach `benchmarking_evidence_ref`. Calculating without one logs a warning and sets `arm_length_documented=False`; that is the honest state, not a failure to be suppressed.

4. **Audit Reporting**: Generate a structured `SignalLicensingAuditReport` — capacity utilisation, remaining headroom, active and revoked subscription ids, consumer entities, and the `pricing_basis` note recording why the Chapter VII simplified approach does not apply.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Asserting Arm's-Length Compliance From The Fee Itself**: A fee being non-zero says nothing about whether it is arm's length. Any boolean named `arm_length_compliant` that is computed from the fee amount is a fabricated compliance opinion that will be read as fact by whoever reads the intercompany file. Report documentation presence, and cite the pricing basis.
- **Applying The 5% Low Value-Adding Mark-Up To Alpha**: The OECD simplified approach explicitly excludes core-business services, R&D services, and anything using or creating unique and valuable intangibles (TPG 2022 paras 7.45, 7.47). A proprietary signal is all three.
- **Un-capped Signal Capacity**: Allowing too many strategy pods to trade the same alpha, degrading realised performance through self-cannibalisation — the pods crowd each other's fills and shorten the signal's half-life.
- **Fail-Open Capacity Arithmetic**: NaN or negative AUM slipping into the subscribed sum silently disables the cap for every subsequent request. Validate at the dataclass boundary, not at the comparison.
- **Overwriting A Live Subscription Id**: Keying subscriptions by id and assigning blindly lets a re-submission erase another pod's grant while its AUM is still counted against capacity, or lets one pod consume headroom twice. Require an explicit revoke first.
- **Retroactive Re-Pricing**: Re-registering a signal with new terms silently changes the fee schedule and cap for subscriptions already granted under the old ones. Existing grants must be re-reviewed, not quietly migrated.
- **Billing Revoked Entitlements**: Fee calculation that ignores `is_active` keeps charging a pod that no longer has access, overstating the intercompany charge in both entities' books.
- **No Loss Carryforward**: A performance share on gross positive PnL with no high-water mark charges the licensee on the same dollars twice across a drawdown-and-recovery cycle.
- **Ignoring Cross-Border Substance**: Transferring proprietary signals between a US research hub and a UK/HK execution desk without an intercompany agreement, a DEMPE analysis of who actually develops and controls the signal, and Chapter V documentation invites adjustment and penalties. Entity name inequality (`is_cross_entity`) is a routing hint, not a jurisdiction determination.
- **Un-tracked Subscriptions**: Consuming proprietary alt-data signals without logging entitlement permissions, violating third-party vendor redistribution and derived-data contracts.
- **Check-Then-Grant Races**: Two concurrent requests can each pass against the same headroom. The engine serialises its own catalog and register; the caller must still serialise its own capital-allocation state.

## Verification

- Instantiate `SignalReuseAndLicensingEngine`. Register `SIG_SENTIMENT_01` (base fee \$10,000, 5% PnL share, max AUM \$50M). Subscribe `Pod_Alpha` (\$20M) and `Pod_Beta` (\$25M) and verify both are entitled. Attempt `Pod_Gamma` at \$15M (total \$60M > \$50M) and verify it is denied *and* not recorded — `get_subscribed_aum` must still read \$45M.
- Verify a request landing exactly on the cap (\$5M against \$45M subscribed) is granted, and one dollar beyond is denied.
- Verify `StrategySubscription(..., allocated_aum_usd=float("nan"))` raises, and that a duplicate `subscription_id` raises `DuplicateRegistrationError` without disturbing the existing grant.
- Verify `revoke_subscription` releases capacity, is idempotent, and that billing the revoked subscription raises `SignalLicensingError`.
- Calculate fee attribution for `Pod_Alpha` with \$1M PnL: \$10,000 + 5% × \$1M = \$60,000 total. Repeat with a \$10M loss carryforward and verify the shareable PnL is \$0, the total is the \$10,000 base fee only, and \$9M carries forward.
- Verify `arm_length_documented` is False when no `benchmarking_evidence_ref` is supplied, regardless of fee size.
- Run `python scripts/test_cross_strategy_signal_reuse_and_licensing.py` (or `python -m unittest discover -s skills/cross-strategy-signal-reuse-and-licensing/scripts`) and confirm a 100% pass rate.

## Related Skills

- `cross-strategy-tax-lot-optimization`
- `transfer-pricing-considerations-for-multi-entity-trading-operations`
- `strategy-capacity-estimation-before-scaling-capital`
- `data-vendor-contractual-usage-restriction-tracking`
- `market-data-entitlement-and-licensing-per-venue`
---
