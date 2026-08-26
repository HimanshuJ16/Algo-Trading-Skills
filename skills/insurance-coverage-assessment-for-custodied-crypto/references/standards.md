# Standards for Crypto Custody Insurance Assessment

## Engineering standards

| Metric | Engineering Standard |
|---|---|
| Policy-to-tier mapping | Each limit MUST be populated from the policy wording confirmed to attach to that wallet tier. Policy form (specie, crime) MUST NOT be inferred to imply a wallet tier. |
| Hot Wallet Coverage Ratio | Coverage for the actively exploitable hot tier SHOULD equal 100% of hot AUM, net of retention. |
| Pooled Dilution | Cold vault coverage MUST account for pro-rata dilution across the custodian's client pool unless a dedicated limit is evidenced by endorsement. |
| Retention | Recovery MUST be computed net of the applicable deductible, and a retention exceeding the firm's own balance MUST be surfaced. |
| Scenario Bracketing | Coverage MUST be reported as a bracket (isolated loss and pooled loss), never as a single figure. |
| Shortfall Reporting | Net uninsured capital MUST be reported in USD and percentage, alongside the assumptions qualifying it. |

The 100% hot and 95% net thresholds are engineering defaults. **No regulator prescribes a
minimum custody insurance coverage ratio**; they encode a conservative treasury posture and
are configurable on the engine.

## Verified source material

Claims in `SKILL.md` and the engine docstrings trace to the following. Insurance
programmes are renewed annually and limits change — re-verify at each review cycle.

### Pooled limits, cold-storage scope, and insolvency (primary source)

Risk-factor and business disclosure in the WisdomTree Bitcoin Fund Form 10-K (FY2025,
accession 0001214659-26-003899), describing Coinbase Custody Trust as bitcoin custodian:

- "The Bitcoin Custodian maintains a commercial crime insurance policy, which is intended
  to cover the loss of client assets **held in cold storage**, including from employee
  collusion or fraud, physical loss including theft, damage of key material, security
  breach or hack, and fraudulent transfer."
- "The insurance maintained by the Bitcoin Custodian **is shared among all of the Bitcoin
  Custodian's customers, is not specific to the Trust** ... and may not be available or
  sufficient to protect the Trust from all possible losses."
- "The Bitcoin Custodian maintains an annually renewed insurance policy in the amount of
  **\$320 million**."
- "the Trust **may be forced to share such insurance proceeds** with other clients or
  customers of the Bitcoin Custodian, which could reduce the amount of such proceeds."
- "the Bitcoin Custodian's insurance **does not cover any loss in value** to bitcoin and
  only covers losses caused by certain events such as fraud or theft."
- Insolvency: client assets "may be considered the property of the bankruptcy estate" and
  clients "may be at risk of being **treated as general unsecured creditors**".
- Neither FDIC nor SIPC protection applies to the custodied assets.
- The custodian "does not disclose to the Sponsor, the percentage of bitcoin that [it]
  holds for customers ... in omnibus cold wallets, as compared to omnibus hot wallets" —
  the basis for treating `total_custodian_cold_aum_usd` as an estimate.

URL: https://www.sec.gov/Archives/edgar/data/1850391/000121465926003899/wtb32026010k.htm

Note this is a **fund's description of its custodian**, not a regulatory requirement. It is
cited as authoritative evidence of market practice, not as a rule.

### Cold-only scope, deductibles, and dedicated loss-payee limits

BitGo's own insurance pages state the \$250M programme covers assets in cold storage where
BitGo holds all keys, does **not** extend to hot wallets or self-managed custody, that BitGo
pays the deductibles, and that clients purchasing Excess Specie Insurance are named
Dedicated Customer Loss Payee.

URLs: https://www.bitgo.com/insurance-faqs/ · https://www.bitgo.com/solutions/insurance/

*Confidence note: these pages were not directly reachable from the audit environment; the
content above comes from search-tool retrieval of those official pages and is consistent
with BitGo's public announcements. Verify against the binder before relying on it.*

### Specie as a class, not a wallet tier

Specie is the Lloyd's market term for insurance of highly valuable moveable property
(bullion, cash, fine art, jewellery), written on an all-risks-of-physical-loss-or-damage
basis. Broker descriptions of digital asset specie wordings extend cover to **cold, warm or
hot wallets** held by custodians or exchanges — which is why policy form cannot be used to
infer wallet tier.

Sources: Price Forbes specie risk solutions (https://www.priceforbes.com/risk-solutions/specie-insurance/);
AXA XL, "Specie insurance: A valuable form of coverage"
(https://axaxl.com/fast-fast-forward/articles/specie-insurance_a-valuable-form-of-coverage).

### Occurrence basis and retention mechanics

Commercial crime policies are generally **occurrence-based**, with the full limit available
for each occurrence, in contrast to aggregate-limit lines. Deductibles frequently apply per
occurrence, so multiple fraudulent transactions treated as separate occurrences can trigger
multiple retentions. How the policy defines "occurrence" therefore materially changes
recovery. This underpins the isolated-loss upper bound.

Sources: Amwins, "Answering the Top 5 Questions on Commercial Crime Insurance"
(https://www.amwins.com/resources-and-insights/market-insights/article/answering-the-top-5-questions-on-commercial-crime-insurance);
CPA Practice Advisor, "Hidden Gaps in Crime Insurance Can Devastate a Balance Sheet"
(2025-05-27).

### Exclusions

Smart contract and DeFi exploits, including flash-loan attacks, are treated as
technological design risk and fall outside these forms; market and price loss, client-side
key loss and phishing, war and terrorism, and regulatory seizure are also standard
exclusions.

**Slashing is not a standard crime or specie exclusion in the sense of being a named
carve-out from otherwise applicable cover — it is simply outside these forms.** Dedicated
institutional slashing cover exists and is increasingly bundled by custodians, but it is
not standardised: verify whether delegator positions (not only validator-operator losses)
are in scope, the per-event cap, and whether correlated slashing events are excluded.

Sources: industry surveys of digital asset custody insurance exclusions (2025-2026);
institutional staking vendor risk guidance (2026).

## Limitations of this reference

Sub-limits per peril, annual aggregate erosion from prior claims, coinsurance, reinstatement
provisions, and custodian capital reserves are not modelled by the engine and are not
covered here. Insurance terms are negotiated per programme; nothing above substitutes for
reading the binder.
