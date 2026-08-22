# Pre-Flight Checklist

- [ ] Is the `Jurisdiction` set explicitly, and does every downstream report state which one it assumed?
- [ ] Are the `TaxElections` recorded from the taxpayer's actual filing positions rather than inferred from trade frequency or a default?
- [ ] Does the treatment match what was filed in prior years? Every election here carries a consistency obligation (CBDT Circular 6/2016, IT-346R, and the irrevocable ITA s.39(4) election).
- [ ] For a Canadian taxpayer, has anyone confirmed the s.39(4) election is even available — it is barred for traders and dealers by s.39(5)?
- [ ] Are trade timestamps timezone-aware, so that session dates come from exchange-local time rather than UTC?
- [ ] For India, does every equity trade carry a real `settled_without_delivery` flag from the contract note? Any same-session-date-proxy warning in the log is a data-quality defect to fix before filing.
- [ ] Are options and futures tagged `AssetClass.DERIVATIVE`, with `is_listed` set so that off-exchange derivatives are not given India's s.43(5) proviso (d) treatment?
- [ ] For US derivatives, is `is_section_1256_contract` set, and are those trades routed to the Section 1256 skill for the 60/40 split rather than reported here?
- [ ] Have holding-period boundaries been spot-checked against the *strict* threshold — a disposal on the one-year anniversary is still short-term?
- [ ] Are algorithm hosting costs, data feeds, and execution fees deducted only against business-income buckets, never against capital gains?
- [ ] Have wash-sale / superficial-loss adjustments and lot-selection method been applied elsewhere in the pipeline, since this engine applies neither?
- [ ] Has a qualified tax adviser in the relevant jurisdiction reviewed the classification basis before anything is filed?
