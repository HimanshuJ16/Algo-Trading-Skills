---
name: insurance-coverage-assessment-for-custodied-crypto
description: >-
  Quantifies how much custodied digital asset capital a custodian's insurance
  programme would actually return, bracketing recovery between an isolated-loss
  upper bound and a pro-rata pooled-loss lower bound, net of retentions, and
  sizing the residual uninsured exposure.
domain: Crypto Custody Security
subdomain: Institutional Treasury Audit & Custody Insurance
tags:
- crypto-insurance
- custody-audit
- specie-policy
- crime-policy
- hot-cold-split
- pooled-limit-dilution
- risk-assessment
brokers_frameworks:
- Institutional Crypto Custodians (BitGo, Fireblocks, Coinbase)
- Lloyd's Specie & Crime Policies
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when sizing counterparty risk against an institutional digital asset custodian (BitGo, Coinbase Custody, Anchorage, Fireblocks, Komainu) and deciding how much capital may sit there. It converts a documented policy structure into a recovery estimate with an explicit upper and lower bound, so a custody allocation rests on an auditable number rather than a headline limit from a marketing page.

The misconception it exists to break is that a custodian's advertised limit is the firm's coverage. It is not, for three independent reasons: the limit is normally **shared across every client**, it attaches only to the **wallet tiers its wording names**, and it pays net of a **retention** that can exceed a smaller client's entire balance.

## When NOT to Use

- **As a coverage opinion.** Whether a given loss is covered turns on the policy wording, read by a broker or coverage counsel. This engine sizes exposure; it does not interpret perils, sub-limits, or exclusions.
- **To assess custodian insolvency risk.** Insurance responds to theft and physical loss, not to a custodian failing. In a bankruptcy, clients may be treated as general unsecured creditors regardless of any coverage ratio computed here. That is a segregation and bankruptcy-remoteness question — see `custody-solution-vendor-due-diligence-checklist`.
- **For self-custody.** With no custodian there is no custodian policy. See `crypto-wallet-key-custody-security`.
- **For DeFi protocol or staking exposure.** Neither is within a crime or specie form. Slashing cover, where it exists at all, is a separate and non-standardised product.

## Prerequisites

- The insurance binder and the executed custody agreement, not the custodian's summary. Confirm from the wording **which wallet tier each policy actually attaches to** before populating any limit.
- A `CustodyInsuranceSpec`: `custodian_name`, `firm_hot_wallet_aum_usd`, `firm_cold_wallet_aum_usd`, `hot_crime_policy_limit_usd`, `cold_specie_policy_limit_usd`, `total_custodian_cold_aum_usd`. Optional: `hot_policy_deductible_usd`, `cold_policy_deductible_usd`, `total_custodian_hot_aum_usd`, `cold_limit_is_dedicated_to_firm`.
- `total_custodian_cold_aum_usd`, the pro-rata denominator. Custodians commonly decline to disclose the omnibus cold/hot split for security reasons, so this is usually an estimate — the engine records that caveat on every report.
- A threshold policy. The engine defaults (100% hot coverage, 95% net coverage) are **engineering defaults with no regulatory basis**; no regulator prescribes a custody insurance coverage ratio.

## Workflow

1. **Map Policy Forms to Wallet Tiers — Never Assume From the Name**: "Specie" is a Lloyd's class for high-value moveable property on an all-risks basis; "crime" is a form covering theft, employee dishonesty, and fraudulent transfer. Either can attach to either tier. Coinbase Custody Trust's **commercial crime** policy covers assets held in **cold storage**; BitGo's cold-only programme does **not** extend to hot wallets. Read the wording, then populate each limit from the policy confirmed to attach to that tier. Where a tier is uninsured, pass `0.0` — that is the real position at several major custodians.
2. **Audit the Hot Tier**: Compute recovery net of the retention, then the coverage ratio against hot AUM. Supply `total_custodian_hot_aum_usd` where known: hot balances sit in omnibus wallets and dilute exactly as cold balances do. Left unset, the hot tier is modelled undiluted and the report flags that as optimistic.
3. **Audit the Cold Tier and Its Pro-Rata Dilution**: Net proceeds `max(0, limit − deductible)` are split across the custodian's cold pool, giving a dilution factor of `min(1, net proceeds ÷ total custodian cold AUM)`; the firm recovers that fraction of its own balance. Set `cold_limit_is_dedicated_to_firm=True` only where an endorsement reserves a limit to the firm — excess specie naming it as dedicated customer loss payee, for instance — since a dedicated limit is not shared.
4. **Read Both Scenarios, Not One Number**: The engine reports an isolated-loss recovery (only this firm is hit, so the per-occurrence limit is not shared) and a pooled-loss recovery (the whole book is hit and the limit is split). Plan against the pooled figure. Where the retention exceeds the firm's own balance the two **invert** — an incident confined to this firm recovers nothing while a pool-wide one still pays a pro-rata share — and that inversion is the signal that the firm is too small for its custodian's retention.
5. **Escalate on Status, Then Re-Verify the Assumptions**: Any hot-tier shortfall is `CRITICAL_HOT_WALLET_UNINSURED`; net pooled coverage below the threshold is `PARTIALLY_INSURED_SHORTFALL`. Classification runs on unrounded ratios, so a 99.996% shortfall cannot round into a clean verdict. Carry the report's `assumptions` list alongside any figure quoted from it — every one of them can move the number.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Mapping Policy Form to Wallet Temperature**: "Specie means cold, crime means hot" is the most common and most expensive error in this audit. Digital asset specie wordings are written for cold, warm **and** hot wallets, and Coinbase's crime policy covers cold storage. Naming the policy tells you nothing about what it attaches to; only the wording does.
- **Reading the Headline Limit as the Firm's Coverage**: Fund disclosures for custodied bitcoin state the custodian's insurance "is shared among all of the ... customers, is not specific to the Trust", and that the fund "may be forced to share such insurance proceeds with other clients". A \$250M limit over a \$1B pool returns 25 cents on the dollar in a pool-wide loss.
- **Planning Against the Pooled Figure Alone**: The pooled number is a lower bound, not the expected outcome. Crime forms are typically occurrence-based with the full limit available per occurrence, so a loss confined to one client can recover far more. Quoting only the worst case will over-provision as surely as quoting only the best case under-provisions.
- **Ignoring the Retention**: An institutional retention can exceed a smaller client's entire balance, leaving it with no practical recovery for an incident affecting only it. Retentions often apply **per occurrence**, so several fraudulent transfers can mean several retentions. Some custodians absorb the deductible themselves — BitGo states it does — but confirm that rather than assuming it.
- **Summing Two Limits That Are One Tower**: Adding a hot limit to a cold limit assumes two independent towers. If the custodian buys a single tower with one shared annual aggregate, total recovery is capped well below the sum, and prior-year claims may already have eroded it. This engine models them as independent — verify from the binder.
- **Treating Insurance as Insolvency Protection**: Coverage responds to theft and physical loss of key material, not to the custodian failing. Fund disclosures warn that in a custodian bankruptcy client assets "may be considered the property of the bankruptcy estate" with clients "at risk of being treated as general unsecured creditors". Neither FDIC nor SIPC protection applies to custodied digital assets.
- **Assuming Smart Contract or Slashing Losses Are Covered**: DeFi protocol exploits and flash-loan attacks are treated as technological design risk and fall outside crime and specie forms. Slashing is not a crime or specie peril either; dedicated slashing cover exists but is not standardised, so check whether delegator as well as validator-operator losses are in scope and whether correlated slashing events — the case that matters — are excluded.
- **Forgetting the Firm Is Not the Insured**: The policy is the custodian's. Absent a loss-payee endorsement the firm holds a contractual claim against the custodian, not a direct claim against the insurer, and recovery depends on the custodian pursuing and passing on the proceeds.

## Verification

- Audit Firm Hot \$2M / Cold \$20M against a \$5M hot limit and a \$250M cold limit over a \$1B custodian pool. Confirm 25% dilution, \$5M pooled cold recovery, 31.82% net pooled coverage, a \$15M shortfall, and `PARTIALLY_INSURED_SHORTFALL` — while the isolated-loss recovery is the full \$22M, bracketing the result.
- Set `cold_limit_is_dedicated_to_firm=True` on the same spec and confirm dilution rises to 100% and cold recovery to \$20M, against \$5M on the shared limit.
- Supply `total_custodian_hot_aum_usd=\$1B` against a \$100M hot limit and \$10M hot AUM, and confirm hot coverage falls to 10% and the status to `CRITICAL_HOT_WALLET_UNINSURED` — the headline limit alone suggests full cover.
- Apply a \$50M cold retention to a \$20M cold book and confirm the scenarios invert: \$0 isolated recovery against \$4M pooled.
- Submit a hot-only firm with a zero cold pool and confirm an audit is produced rather than a `ZeroDivisionError`; submit a NaN AUM, a negative limit, or a firm cold balance exceeding the custodian pool and confirm `CustodyInsuranceError` rather than a `FULLY_INSURED` verdict.
- Confirm 999,960 of cover against 1,000,000 of hot AUM classifies `CRITICAL_HOT_WALLET_UNINSURED` even though it displays as 100.0%.
- Run `python -m unittest discover -s skills/insurance-coverage-assessment-for-custodied-crypto/scripts` and confirm a 100% pass rate.

## Related Skills

- `custody-solution-vendor-due-diligence-checklist`
- `third-party-custody-audit-report-review-cadence`
- `hot-cold-wallet-split-for-trading-bots`
- `custodial-vs-non-custodial-tradeoff-assessment`
