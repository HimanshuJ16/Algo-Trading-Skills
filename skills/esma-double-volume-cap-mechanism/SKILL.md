---
name: esma-double-volume-cap-mechanism
description: Use when gating EU dark routing under the MiFIR Article 5 volume cap —
  the single 7% Union-wide cap on the reference price waiver that replaced the 4%/8%
  Double Volume Cap, enforced from the ESMA-published quarterly suspension register.
domain: Venue Integration & Protocols
subdomain: European Regulatory Compliance (MiFID II / MiFIR)
tags:
- esma
- single-volume-cap
- mifid-ii
- mifir-article-5
- dark-pools
- reference-price-waiver
- lis-waiver
- smart-order-router
brokers_frameworks:
- MiFIR Art. 5 (as amended by Reg. (EU) 2024/791)
- ESMA Volume Cap Mechanism register
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in EU/EEA Smart Order Routers and dark execution adapters, as the waiver gate that runs *before* venue selection. The slug is historical: the Double Volume Cap it names no longer exists. Regulation (EU) 2024/791 (the MiFIR review, in force 28 March 2024) replaced it with a **single Union-wide cap of 7%** applying to the **reference price waiver alone** (MiFIR Art. 4(1)(a)). ESMA's September 2025 file was the last DVC publication, every DVC suspension expired on 28 September 2025, and the monitored period for the new cap began 29 September 2025 (Art. 5(8)). The first Volume Cap results file was published 9 October 2025, with suspensions running 14 October 2025 to 13 January 2026, and ESMA publishes quarterly thereafter.

Reach for it when you need to answer one question per order: *may this order use the waiver it is asking for today?*

## When NOT to Use

- **As a source of the suspension status.** Art. 5(1) requires venues to suspend "on data published by ESMA". The ratio this engine computes from your own volume numbers is an early-warning estimate; it will not reproduce ESMA's methodology, which since January 2026 is derived from transaction reports collected by national competent authorities rather than the decommissioned venue-reported DVC feed. Supply the published suspension file.
- **To block negotiated trades, LIS trades or order-management-facility orders.** Article 5 reaches Art. 4(1)(a) only. Blocking an Art. 4(1)(b)/(c)/(d) order because of the volume cap is over-compliance that costs execution quality and buys nothing.
- **To decide a venue.** This gates a waiver. Venue choice, sizing and MinQty belong to `dark-pool-routing-logic`.
- **Outside the EU/EEA.** No non-EU regime is modelled; the UK ran its own DVC and then removed it.
- **On pre-29-September-2025 data, for anything but a backtest.** The engine selects legacy 4%/8% DVC semantics automatically for those dates and labels every report `LEGACY_DVC`. That path is for historical analysis only.

## Prerequisites

- The **ESMA Volume Cap suspension file** for the current quarter (`EsmaSuspensionRegister`: publication date plus per-ISIN suspension start/end dates). Without it the engine fails closed.
- Rolling 12-month **total EU volume** and **Union volume executed under the reference price waiver** for the instrument, in EUR. The numerator is RPW volume, *not* total dark volume.
- The order: `order_id`, `isin`, `symbol`, `order_val_eur`, and `intended_waiver_type` — one of `RPW`, `NTW`, `LIS`, `OMF`.
- For a `LIS` order, that instrument's **large-in-scale threshold** (`lis_threshold_eur`). There is no default; RTS 1 Annex II Table 1 bands it from €15,000 to €650,000 by average daily turnover, and `rts1_lis_threshold_eur(adt, "SHRS")` computes it.
- `as_of` — the trading date being evaluated. It selects the regulatory regime.

## Workflow

1. **Load the ESMA register, and check its age.**
   - Publication is quarterly (Art. 5(4): within seven working days of the end of March, June, September and December). A file older than `max_register_age_days` (default 100, sized from that cadence — not a figure from the Regulation) cannot describe the current quarter.
   - **Decision point — an empty register is not a missing register.** A file that lists no suspensions is a positive statement that nothing is suspended. Passing no file at all is an *unknown* status, and the engine blocks the capped waiver and sends the order lit. Fail closed is the default because routing lit is always lawful; routing dark against an unknown suspension status is not.

2. **Resolve the waiver actually available for this order.**
   - **Decision point — a `LIS` claim below the instrument's threshold is not large in scale.** It is downgraded to `RPW` and re-evaluated against the cap, not waved through. Size alone never confers the Art. 4(1)(c) exemption.
   - `NTW` and `OMF` are outside Article 5 under the current regime and are approved without consulting the register.

3. **Compute the Union RPW share and the headroom** (monitoring only, never the decision):
   - $\text{Union RPW Share \%} = \frac{\text{Rolling 12m Union RPW Volume}}{\text{Rolling 12m Total EU Volume}} \times 100\%$
   - $\text{Headroom} = \frac{\text{Cap \%}}{100} \times \text{Total EU Volume} - \text{Union RPW Volume}$ — the EUR of further RPW volume the name can absorb before the cap. Negative once it is through.
   - **Decision point — compare unrounded, and strictly.** Art. 5(1) says "exceeds 7%". A share of 6.996% rounded to two decimals reads 7.00% and manufactures a suspension; a share of exactly 7.000% is *within* the cap. The engine cross-multiplies ($u \times 100 > c \times t$) so neither rounding nor floating-point error can tip a name over its own cap.

4. **Route.** If the cap applies and either the register says `SUSPENDED`, or the official status is unknown (`REGISTER_NOT_SUPPLIED` / `REGISTER_STALE`), or the internal estimate shows a breach — block and return `LIT_VENUE`. Otherwise return `DARK_RPW`. `block_rpw_on_estimated_breach=False` turns off the third, pre-emptive limb where you want ESMA's file to be the sole authority.

5. **Emit `VolumeCapAuditReport`** carrying the estimate and the official status as *separate* fields, plus the suspension end date, so the audit trail records which of the two drove the decision.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Still enforcing 4% and 8%.** Those thresholds were repealed. Applying the 4% per-venue limb to a live order today blocks dark liquidity that MiFIR permits; applying 8% Union-wide lets a name through that breached the 7% cap 1.5 percentage points earlier.
- **Capping the negotiated trade waiver.** The pre-2024 DVC covered Art. 4(1)(a) *and* the liquid-instrument negotiated trade waiver Art. 4(1)(b)(i). The single cap covers Art. 4(1)(a) only. Carrying the old scope forward is a self-inflicted execution cost.
- **Treating any large order as LIS-exempt.** There is no flat €100,000 large-in-scale threshold. A €200,000 order in a name whose RTS 1 threshold is €650,000 is not large in scale, and executing it dark under a suspension is a breach — dressed up as an exemption.
- **Using total dark volume as the numerator.** LIS and negotiated dark volume do not count toward the Article 5 cap. Feeding total dark volume in overstates the ratio and blocks names ESMA has not suspended.
- **Rounding before comparing to the cap.** Round for the report, never for the decision — see step 3.
- **Substituting your own calculation for ESMA's file.** The estimate is a planning tool. A name can be suspended while your ratio looks clean (ESMA's data is not yours), and vice versa; the register wins in both directions.
- **Letting the register go stale.** Suspensions run three months and files publish quarterly. A file from two quarters ago reads as "nothing suspended" unless age is checked — the most dangerous silent failure in this workflow, because it fails *open*.
- **Assuming a suspension ends when your file expires.** Suspension end dates are per-instrument and published; they are inclusive of the end date (suspensions run from 08:00 CET on the start date to the close of the trading day on the end date).

## Verification

- Instantiate `EsmaVolumeCapEngine()`. On €1bn rolling total EU volume with €70,000,000 Union RPW volume (**exactly** 7.0000%) and a clean register, verify `internal_estimate_status == "ESTIMATED_WITHIN_CAP"` and `final_routed_venue_type == "DARK_RPW"` — the cap is exceeded, not met. Repeat at €69,960,000 (6.996%, which rounds to 7.00%) and confirm it still routes dark. At €85,000,000 (8.5%) confirm `LIT_VENUE`.
- Supply a register whose only suspension covers the ISIN and dates: verify a **1.0%** name is still blocked, proving the register outranks the internal estimate. Omit the register entirely, and separately age it past 100 days: verify both fail closed to `LIT_VENUE`.
- Submit `intended_waiver_type="LIS"`, `order_val_eur=200_000`, `lis_threshold_eur=650_000` against a suspended name: verify `effective_waiver_type == "RPW"` and `final_routed_venue_type == "LIT_VENUE"`. Raise the order to €650,000: verify `DARK_LIS_EXEMPT`.
- Submit `NTW` and `OMF` against the same suspended name: verify both route dark with `is_cap_applicable` false.
- Verify `rts1_lis_threshold_eur` reproduces RTS 1 Annex II Table 1 at every band edge (€15,000 below €50k ADT through €650,000 at or above €100m ADT), and raises for `"ETFS"` rather than guessing.
- Run `python -m unittest discover -s skills/esma-double-volume-cap-mechanism/scripts`.

## Related Skills

- `dark-pool-routing-logic`
- `mifid-ii-algo-trading-compliance-eu`
- `deutsche-borse-xetra-api-integration`
- `eu-short-selling-regulation-disclosure-thresholds`
