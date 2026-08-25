---
name: esg-data-signal-research-and-vendor-comparison
description: Cross-vendor ESG rating normalization engine for MSCI, Morningstar
  Sustainalytics and LSEG/Refinitiv scores, computing consensus scores, vendor
  disagreement dispersion, rule-based exclusion flags and ESG factor overlay
  research signals.
domain: Quantitative Research & Alternative Data
subdomain: ESG Data & Factor Investing
tags:
- esg-data
- alternative-data
- msci-esg
- sustainalytics
- lseg-refinitiv-esg
- vendor-reconciliation
- rating-divergence
brokers_frameworks:
- MSCI ESG Ratings
- Morningstar Sustainalytics ESG Risk Ratings
- LSEG ESG Scores (formerly Refinitiv)
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in sustainable quantitative investing, multi-factor portfolio construction, and ESG risk overlays, when you hold ESG assessments from more than one vendor for the same issuer and need a single defensible number plus an explicit measure of how much the vendors disagree.

ESG ratings from different providers measure different things. Berg, Kölbel & Rigobon (2022, *Review of Finance* 26(6), 1315–1344) report pairwise correlations between six major raters ranging from **0.38 to 0.71**, and decompose the divergence into measurement (56%), scope (38%) and weight (6%). Because scope alone accounts for well over a third of it, the disagreement between vendors is itself information — a name the vendors cannot agree on is a name whose ESG score should not carry conviction in either direction.

This module normalizes each vendor's native scale to $[0.0, 1.0]$ using that vendor's own published band structure, computes the consensus $\bar{S}$ and the dispersion $\sigma_{\text{esg}}$, gates directional signals on both vendor coverage and vendor agreement, and carries rule-based exclusion flags through to the audit report.

## When NOT to Use

- **As a standalone trading signal.** This is a research feature generator. It has no return model, no position sizing, and no calibration. The $0.75$ / $0.30$ / $0.25$ thresholds are illustrative defaults, not empirically validated constants.
- **To build a cross-sectional ESG factor from nominal vendor scales.** The three vendors use incompatible reference frames (see Prerequisites). Rescaling nominal scales makes them arithmetically averageable, not conceptually comparable. For a cross-sectional factor, rank each vendor's raw scores *within your universe on a common date* and feed those percentiles in via `msci_rating_map` / pre-normalized inputs. This engine scores one issuer at a time and cannot compute universe percentiles for you.
- **As a compliance screen.** `EXCLUDED_SECTOR` reports exclusions you have already determined elsewhere. The engine evaluates no revenue thresholds, no treaty lists, and no UNGC/OECD violation status. A high consensus ESG score is not evidence that an issuer passes an exclusion screen — the two are independent tests.
- **On a single vendor.** With one opinion the dispersion is zero by construction; "the vendors agree" is unfalsifiable. The engine returns `INSUFFICIENT_VENDOR_COVERAGE` rather than a signal.
- **On a mixed-vintage vendor panel in a backtest** without a point-in-time store. See the look-ahead pitfall below.

## Prerequisites

- Security identifier (`ticker`) and raw vendor scores in **vendor-native units** — the engine does the normalization, so do not pre-scale.
- Awareness that the three inputs are benchmarked differently, which is the *scope* component of the divergence above:
  - **MSCI ESG Ratings** — seven letter bands `AAA`…`CCC`, derived from a 0–10 final Industry-Adjusted Score split into seven equal 1.4286-wide bands. MSCI states these are "explicitly intended to be interpreted relative to a company's **industry peers**."
  - **Morningstar Sustainalytics ESG Risk Rating** — unmanaged-risk score where **0 is best**. Published bands: Negligible 0–9.99, Low 10–19.99, Medium 20–29.99, High 30–39.99, Severe **40 and higher**. Sustainalytics states "These risk categories are **absolute**, i.e. comparable across sectors."
  - **LSEG ESG Scores (formerly Refinitiv / ASSET4)** — 0–100 where 100 is best, benchmarked against the TRBC industry group for environmental and social categories and against the **country of incorporation** for governance.
- Rule-based exclusion status determined upstream, passed as `has_controversial_weapons` and/or `exclusion_reasons`.
- A timezone-aware `as_of` vintage stamp if the output feeds a backtest. Naive datetimes are rejected.
- Python 3.7+ standard library only. No third-party dependency.

## Workflow

1. **Vendor Score Normalization** — each vendor is mapped to $[0.0, 1.0]$ using its own published band structure, not a nominal min/max:
   - **MSCI**: the letter identifies an *interval* of the 0–10 Industry-Adjusted Score, so the point estimate is the **band mid-point**, $(2k+1)/14$ for $k=0$ (`CCC`) to $6$ (`AAA`) — `CCC` $=0.0714$, `BBB` $=0.5$, `AAA` $=0.9286$. Mapping the letters to band *end-points* ($\text{AAA}=1.0$, $\text{CCC}=0.0$) overstates both tails by ~7 points and mechanically inflates dispersion against continuously-scored vendors.
   - **Sustainalytics**: $1.0 - \frac{\min(\text{Risk},\,40)}{40}$. The rescale is bounded at the **Severe-band floor**, not at 100. Severe Risk is open-ended above 40, so 100 is a nominal bound nobody observes; dividing by it would map a Severe issuer scoring 45 to $0.55$ — above the mid-point of the normalized scale — and no realistic issuer could ever reach the laggard band. Override with `sustainalytics_severe_threshold` if your universe justifies a different floor.
   - **LSEG/Refinitiv**: $\frac{\text{Score}}{100}$ — already reported on the target scale.
   - An unrecognised MSCI token (`'A+'`, `'AAAA'`), a NaN, an infinity, or an out-of-range score **raises** rather than returning `None` or clipping. `None` means "this vendor does not cover this issuer" and nothing else.
2. **Consensus & Dispersion Calculation** over the $K$ covering vendors:
   - $\bar{S} = \frac{1}{K}\sum S_k$; $\quad \sigma_{\text{esg}} = \sqrt{\frac{1}{K}\sum (S_k - \bar{S})^2}$ (population form, deviations taken about the *unrounded* mean).
   - Both are `None` when undefined — $\bar{S}$ with no coverage, $\sigma_{\text{esg}}$ with a single vendor. They are never reported as $0.0$, because a downstream factor ranker reads a zero consensus as the worst ESG name in the universe rather than as "not measured".
3. **Signal Classification** (precedence order, first match wins):
   - `EXCLUDED_SECTOR` — any rule-based exclusion was already failed. Overrides every consensus score; the normalized scores are still reported so the audit trail survives.
   - `INSUFFICIENT_VENDOR_COVERAGE` — fewer than `min_vendors_for_conviction` (default 2) vendors cover the issuer.
   - `NEUTRAL_HIGH_DISAGREEMENT` — $\sigma_{\text{esg}} > 0.25$. Applied **symmetrically**: vendors that cannot agree support a laggard call no more than they support a leader call.
   - `BULLISH_ESG_LEADER` — $\bar{S} \ge 0.75$.
   - `BEARISH_ESG_LAGGARD` — $\bar{S} \le 0.30$.
   - `NEUTRAL` — otherwise.
4. **Threshold Calibration** — re-estimate all four thresholds per universe (region, sector, market cap, vendor panel) on out-of-sample data before trading them.
5. **Audit Report Generation** — output `EsgSignalAuditReport`, carrying the normalized per-vendor scores, `vendor_count`, `exclusion_reasons` and the `as_of` vintage.

> Full procedure: see `references/workflows.md`.
> Standards and regulatory touchpoints: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Naively Averaging Un-Normalized Ratings**: Averaging an MSCI letter with a Sustainalytics *risk* number is not just a scale error — the two run in opposite directions, so the arithmetic silently rewards the riskiest names.
- **Rescaling by a Nominal Maximum Nobody Observes**: Sustainalytics' theoretical ceiling is 100, but its Severe band starts at 40 and is open-ended. Dividing by 100 compresses the entire realised distribution into roughly $[0.55, 1.0]$, biases every consensus upward, and inflates dispersion against vendors that do use their full range.
- **Mapping Ordinal Letters to Band End-Points**: `AAA` does not mean "a perfect 10". It means the Industry-Adjusted Score fell somewhere in $[8.571, 10.0]$. Assigning it $1.0$ asserts the top of that interval for every AAA-rated issuer.
- **Averaging Across Incompatible Reference Frames**: MSCI is industry-relative, Sustainalytics is explicitly absolute, LSEG benchmarks governance against the country of incorporation. A consensus across the three is a blend of three different questions; Berg et al. attribute 38% of total divergence to this scope effect. State it as a documented limitation rather than assuming it away.
- **Treating a Single Vendor as Corroboration**: One score yields $\sigma_{\text{esg}} = 0$ by construction. Reporting that as "low vendor disagreement" claims agreement that was never observed.
- **Gating Only the Bullish Side on Disagreement**: If the leader signal requires vendor agreement but the laggard signal does not, an issuer rated `CCC` by one vendor and 80/100 by another emits a confident short-side ESG signal off maximal disagreement.
- **Silent NaN and Out-of-Range Propagation**: A NaN in a 0–100 field clips to a *boundary* under `max(0, min(100, x))`, so a corrupt feed becomes a perfect score rather than an error. Reject non-finite and out-of-range values at the vendor boundary.
- **Fabricating a Zero for Missing Data**: Reporting `consensus = 0.0` for an uncovered or excluded issuer places it at the bottom of any cross-sectional ESG rank. Uncovered is not the same as bad.
- **Look-Ahead Bias from Retroactively Rewritten ESG History**: ESG vendors restate their own past. Berg, Fabisik & Sautner (ECGI Finance Working Paper 708/2020) document that Refinitiv ESG rewrote historical scores such that median overall scores in the rewritten data were **18% lower** than in the original vintage (E −44%, S −16%, G −7%), that firms' ESG quantile classifications changed as a result, and that the ESG–return relationship present in the rewritten data is **absent in the initial data**. They conclude the rewriting is "an ongoing rather than a one-off phenomenon." Backtest against the vintage that was actually observable on the trading date; stamp every record with `as_of`.
- **Survivorship Bias in Vendor Coverage**: Today's vendor universe omits issuers that were delisted, acquired, or simply not yet covered. Backtesting on it overstates coverage and biases the sample toward large, well-disclosed issuers.
- **Methodology-Version Breaks Inside a Backtest Window**: Vendors periodically replace the model, not just the data. LSEG's ESG Scores methodology effective 1 July 2026 describes an "absolute assessment" model, where earlier Refinitiv documentation described percentile-rank scoring. A series spanning such a change is two different variables sharing one column name.

## Verification

- Instantiate `EsgDataSignalEngine`. Submit MSCI `'AAA'` ($0.9286$), Sustainalytics `8.0` ($0.8$), LSEG/Refinitiv `85.0` ($0.85$): consensus $0.8595$, dispersion $0.0529$, signal `BULLISH_ESG_LEADER`.
- Submit conflicting ratings — MSCI `'AAA'` ($0.9286$) against Sustainalytics `30.0` ($0.25$): consensus $0.5893$, dispersion $0.3393$, signal `NEUTRAL_HIGH_DISAGREEMENT`.
- Confirm the disagreement gate is symmetric: MSCI `'CCC'` ($0.0714$), Sustainalytics `40.0` ($0.0$), LSEG/Refinitiv `80.0` ($0.8$) gives consensus $0.2905$ — inside the laggard band — but dispersion $0.3615$, so the emitted signal is `NEUTRAL_HIGH_DISAGREEMENT`, not `BEARISH_ESG_LAGGARD`.
- Confirm a single vendor returns `INSUFFICIENT_VENDOR_COVERAGE` with `vendor_disagreement_dispersion is None`, and that an uncovered issuer returns `consensus_esg_score is None` rather than $0.0$.
- Confirm `normalize_refinitiv(float('nan'))` and `normalize_refinitiv(500.0)` both raise `ValueError`, and that `normalize_msci('A+')` raises `ValueError` rather than returning `None`.
- Confirm `has_controversial_weapons=True` yields `EXCLUDED_SECTOR` even against an `AAA` / 95 panel, while the normalized per-vendor scores remain populated in the report.
- Run `python -m unittest discover -s skills/esg-data-signal-research-and-vendor-comparison/scripts`.

## Related Skills

- `alternative-data-vendor-due-diligence-checklist`
- `data-vendor-cross-validation-for-backtests`
- `point-in-time-fundamentals-data-joins`
- `survivorship-bias-free-universe-construction`
- `reference-data-symbol-mapping-across-vendors`
