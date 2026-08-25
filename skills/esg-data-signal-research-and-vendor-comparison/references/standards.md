# Standards for ESG Data Signal Research and Vendor Comparison

## Engineering standards

| Metric | Engineering Standard |
|---|---|
| Scale Normalization | ALL vendor ESG assessments MUST be normalized to $[0.0, 1.0]$ using the **vendor's own published band structure**, never a nominal min/max that the vendor's realised distribution does not reach. |
| Ordinal Rating Mapping | A letter rating identifies an interval, not a point. Letter scales MUST be mapped to band **mid-points**, not band end-points. |
| Missing vs Malformed | `None` MUST mean "vendor does not cover this issuer". A malformed, non-finite, or out-of-range value MUST raise, never be coerced, clipped, or silently dropped from the consensus. |
| Undefined Aggregates | Consensus and dispersion MUST be reported as `None` when undefined (no coverage; single vendor). They MUST NOT be reported as $0.0$ — a downstream cross-sectional ranker reads a zero as the worst ESG name in the universe. |
| Vendor Coverage Floor | A directional signal MUST require at least two covering vendors. A single opinion produces $\sigma_{\text{esg}} = 0$ by construction and cannot evidence agreement. |
| Vendor Disagreement Threshold | Dispersion $\sigma_{\text{esg}} > 0.25$ MUST suppress the directional signal **symmetrically** — for laggard calls as well as leader calls. |
| Exclusion Guard | Rule-based exclusions MUST override any consensus score, and MUST NOT be inferred from one. The normalized per-vendor scores MUST still be retained on an excluded record for audit. |
| Point-in-Time Vintage | Any record feeding a backtest MUST carry a timezone-aware `as_of` vintage stamp, because ESG vendors restate history. |

## Vendor scale references

| Vendor | Scale | Reference frame | Source |
|---|---|---|---|
| MSCI ESG Ratings | Seven letter bands `AAA`…`CCC` from a 0–10 final Industry-Adjusted Score, split into seven equal 1.4286-wide bands (CCC 0.0–1.429 … AAA 8.571–10.0) | Industry-relative — "explicitly intended to be interpreted relative to a company's industry peers" | MSCI ESG Ratings Methodology, March 2026, §2.1 "Rating & scores", Exhibit 2. <https://www.msci.com/documents/1296102/34424357/MSCI+ESG+Ratings+Methodology.pdf> |
| Morningstar Sustainalytics ESG Risk Rating | Unmanaged-risk score, 0 best. Negligible 0–9.99, Low 10–19.99, Medium 20–29.99, High 30–39.99, Severe **40 and higher** (open-ended) | Absolute — "These risk categories are absolute, i.e. comparable across sectors" | Sustainalytics, "The ESG Risk Rating: Frequently Asked Questions – For Companies", August 2020. <https://connect.sustainalytics.com/hubfs/SFS/Sustainalytics%20ESG%20Risk%20Rating%20-%20FAQs%20for%20Corporations.pdf> |
| LSEG ESG Scores (formerly Refinitiv / ASSET4) | 0–100, 100 best | Environmental and social categories benchmarked against the TRBC industry group; governance against the country of incorporation | LSEG ESG Scores Methodology, July 2026 (model v1.0, effective 1 July 2026). <https://www.lseg.com/content/dam/data-analytics/en_us/documents/methodology/lseg-esg-scores-methodology-final.pdf> |

## Research references

| Claim | Source |
|---|---|
| Pairwise correlations between six major ESG raters range from **0.38 to 0.71**; divergence decomposes into measurement 56%, scope 38%, weight 6% | Berg, F., Kölbel, J. F., & Rigobon, R. (2022). "Aggregate Confusion: The Divergence of ESG Ratings." *Review of Finance* 26(6), 1315–1344. <https://academic.oup.com/rof/article/26/6/1315/6590670> |
| ESG vendors retroactively rewrite historical scores. Median overall scores in Refinitiv ESG's rewritten data were **18% lower** than the original vintage (E −44%, S −16%, G −7%); firms' ESG quantile classifications changed; the ESG–return relationship present in the rewritten data is absent in the initial data; the rewriting is "ongoing rather than a one-off phenomenon" | Berg, F., Fabisik, K., & Sautner, Z. (2021). "Is History Repeating Itself? The (Un)predictable Past of ESG Ratings." ECGI Finance Working Paper No. 708/2020. <https://ssrn.com/abstract=3722087> |

## Regulatory touchpoints

These are **jurisdiction-specific** and apply to particular activities, not to ESG research in general. None of them are enforced by this skill's code; they determine whether and how you may use or redistribute the vendor inputs.

### EU — Regulation (EU) 2024/3005 (ESG Ratings Regulation)

Regulation (EU) 2024/3005 of 27 November 2024 on the transparency and integrity of ESG rating activities **applies from 2 July 2026**, with ESMA as direct supervisor of ESG rating providers offering services in the Union.

Relevant to a consumer of vendor ESG ratings:

- Providers issuing and publishing or distributing ESG ratings in the Union must be authorised by ESMA. Under the Article 51 transition, existing providers had to notify ESMA of their intention to apply by **2 August 2026** and apply by **2 November 2026** (small providers: notify by 2 November 2026).
- **Article 2(2)(k)** exempts ESG ratings issued by an authorised provider where those ratings are subsequently published or distributed by a third party — e.g. a fund manager reproducing a provider's ratings in marketing material.
- ESMA's public statement of 1 July 2026 confirms that from 2 July 2026 third parties may continue publishing or distributing the ratings of not-yet-authorised providers until ESMA decides on the application, but that **after 2 November 2026** a third party may no longer publish or distribute a provider's ESG ratings unless that provider has applied/notified and appears in the **Article 14 register** on ESMA's website.
  - ESMA84-1427279869-1396, "Publication or distribution of ESG ratings by third parties in the period from 2 July 2026 until authorisation, recognition or registration of ESG rating providers", 1 July 2026. <https://www.esma.europa.eu/esmas-activities/investors-and-issuers/esg-rating-providers>

**Practical consequence:** if any client-facing artefact reproduces vendor ESG ratings inside the EU, check the ESMA Article 14 register for each vendor before distribution. Whether a derived internal signal falls inside or outside scope is a legal question for counsel, not one this skill answers — the Regulation's scope turns on issuing, publishing and distributing ESG ratings.

### EU — exclusion screens are rule-based, not score-based

`EXCLUDED_SECTOR` carries an exclusion determined upstream. The mandates it typically stands in for are threshold rules that no consensus ESG score can substitute for:

- **Commission Delegated Regulation (EU) 2020/1818, Article 12** (EU Paris-aligned Benchmarks) requires administrators to exclude companies involved in controversial weapons; in the cultivation and production of tobacco; found in violation of the UN Global Compact principles or the OECD Guidelines for Multinational Enterprises; deriving ≥1% of revenues from hard coal and lignite; ≥10% from oil fuels; ≥50% from gaseous fuels; and ≥50% from electricity generation with a GHG intensity above 100 gCO2e/kWh. This binds **benchmark administrators** of EU PABs, not every investor.
- **Commission Delegated Regulation (EU) 2022/1288** (SFDR RTS), Annex I Table 1, mandatory principal adverse impact indicator **14**, "Exposure to controversial weapons (anti-personnel mines, cluster munitions, chemical weapons and biological weapons)". This binds **financial market participants** subject to SFDR PAI reporting.

Neither is universal. Do not apply an EU threshold to a non-EU mandate, and do not treat a passing consensus ESG score as evidence that either screen is satisfied.
