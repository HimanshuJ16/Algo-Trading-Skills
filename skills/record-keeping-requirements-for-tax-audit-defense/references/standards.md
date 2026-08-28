# Standards for Record-Keeping Requirements for Tax Audit Defense

**Jurisdiction: US federal income tax.** These standards do not apply to other
tax regimes. Sources verified August 2026.

## Engineering standards

| Metric | Engineering Standard |
|---|---|
| Mandatory Fields | Every trade record MUST carry `trade_id`, `symbol`, `side`, `quantity`, `price`, `trade_date`, `cost_basis_usd`. Missing basis invites a zero-basis assertion taxing gross proceeds. |
| Record Identity | `trade_id` MUST be unique across the record set; duplicates break attribution to a single execution. |
| Holding Period | Long-term requires disposal **strictly after** the one-year anniversary of acquisition. Day counts alone: $\le 365$ short-term, $\ge 367$ long-term, $366$ **ambiguous**. |
| Wash Sale Determination | Every capital-account sell MUST carry a recorded determination, including a negative one. A determination made before the sale date $+30$ days is provisional. |
| Lot Identification | A `SPECIFIC_ID` claim MUST be supported by an identification made no later than the settlement deadline (T+1 for most US securities since 2024-05-28). Otherwise basis reverts to FIFO. |
| § 475(f) Segregation | Under a mark-to-market election, investment securities MUST be identified as such in the records **on the day acquired**. |
| Retention Clock | Retention runs from the **disposal** date, never the acquisition date. A record with no known disposal date has no computable purge date. |
| Legal Hold | A record under examination, litigation or preservation hold is never purge-eligible regardless of age. |
| Determinism | Audits MUST be evaluated against an explicit `as_of` date so results are reproducible. |

## Limitation periods (IRS)

The commonly cited "7-year rule" is not a general requirement. The applicable
period depends on return-level facts:

| Situation | Period | Authority |
|---|---|---|
| General assessment period | 3 years from filing | IRC § 6501(a) |
| Claim for credit or refund | 3 years from filing, or 2 years from payment, whichever is later | IRC § 6511(a) |
| Omission of more than 25% of gross income | 6 years | IRC § 6501(e) |
| Loss from worthless securities or bad debt | 7 years | IRC § 6511(d)(1) |
| No return filed | Unlimited | IRC § 6501(c)(3) |
| Fraudulent return | Unlimited | IRC § 6501(c)(1) |
| Property (incl. securities) records | Until the limitations period expires **for the year of disposal** | IRS, "How long should I keep records?" |
| Employment tax records | At least 4 years after the tax is due or paid, whichever is later | IRS, ibid. |

The engine's `retention_years=7` default is a conservative firm **policy**
setting covering the worthless-securities case. It is not asserted as a
statutory floor, and it does not model the § 6501(e)/(c) extensions.

## Adjacent regimes (do not conflate)

| Regime | Scope | Periods |
|---|---|---|
| SEC Rule 17a-4 | Registered broker-dealers under the Exchange Act — **not** taxpayers generally | 3 or 6 years by record class; 2022 amendments added an audit-trail alternative to WORM (effective 2023-01-03, compliance 2023-05-03) |
| IRS Rev. Proc. 98-25 | Machine-sensible records in an ADP system, under IRC § 6001 / Treas. Reg. § 1.6001-1. Mandatory for taxpayers with $10M+ in assets, and for smaller taxpayers whose records are not in hardcopy or whose machine calculations cannot be verified manually | Records must reconcile to books and return with a documented audit trail |
| IRS Rev. Proc. 97-22 | Electronic storage systems (imaging hardcopy, converting computerised records) | — |

## Sources

- IRS, "How long should I keep records?" — https://www.irs.gov/businesses/small-businesses-self-employed/how-long-should-i-keep-records
- IRS Publication 550, *Investment Income and Expenses* (Holding Period; Wash Sales) — https://www.irs.gov/publications/p550
- IRS Tax Topic 429, *Traders in Securities* (§ 475(f) mark-to-market) — https://www.irs.gov/taxtopics/tc429
- IRS, *Automated records* (Rev. Proc. 98-25, 97-22, 81-46) — https://www.irs.gov/businesses/automated-records
- IRS Rev. Proc. 98-25 — https://www.irs.gov/pub/irs-drop/rp-98-25.pdf
- Treas. Reg. § 1.1012-1(c), *Basis rules of general application* — https://www.ecfr.gov/current/title-26/chapter-I/subchapter-A/part-1/subject-group-ECFR5a368d8ba363b3c
- SEC, *Amendments to Electronic Recordkeeping Requirements for Broker-Dealers* — https://www.sec.gov/investment/amendments-electronic-recordkeeping-requirements-broker-dealers
- SEC, *Shortening the Securities Transaction Settlement Cycle* (Rule 15c6-1, T+1 from 2024-05-28) — https://www.sec.gov/exams/educationhelpguidesfaqs/t1-faq
