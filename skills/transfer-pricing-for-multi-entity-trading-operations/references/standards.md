# Transfer Pricing Standards for Multi-Entity Trading Operations

All paragraph references are to the **OECD Transfer Pricing Guidelines for Multinational
Enterprises and Tax Administrations, January 2022** unless stated otherwise. Every figure
below is either quoted from a primary source or explicitly marked as not being a published
figure. Nothing here is a substitute for a benchmarking study or local advice.

## 1. Method selection for the intercompany flows a trading group actually has

| Method | Where it fits in a trading group | What makes the number arm's length |
| :--- | :--- | :--- |
| **Cost Plus** (Ch. II Part II) | Middle/back-office support, compliance, IT helpdesk | A benchmarked gross markup on the cost of providing the service |
| **CUP** (Ch. II Part II) | Order execution routing, market data resale, exchange fee recharges | An observable third-party price for the same service in comparable circumstances |
| **TNMM** (Ch. II Part III Section B) | Manager entities, quant research entities where no CUP exists | A benchmarked *net* profit level indicator — net cost-plus markup, operating margin, or Berry ratio |
| **Transactional Profit Split** (Ch. II Part III Section C) | Global trading PnL where both entities make unique and valuable contributions | Relative contributions, measured by an allocation key that reflects value creation |

There is no hierarchy that makes profit split a fallback. It applies where each party makes
unique and valuable contributions or the operations are highly integrated — which is a
plausible description of an IP entity and an execution entity in the same strategy, and an
implausible one for a payroll recharge.

## 2. Formulas actually implemented

1. **Cost Plus fee**

   $$\text{Fee} = \text{Cost base} \times \left(1 + \frac{\text{Markup \%}}{100}\right)$$

   The cost base is a transfer pricing decision. Pass-through costs are excluded from the
   marked-up pool under the OECD simplified approach (paras. 7.34, 7.61); the IRAS routine
   support services concession requires *all* costs relating to the service to be included.

2. **CUP fee**

   $$\text{Fee} = \text{Volume (orders / shares / messages)} \times \text{Third-party unit price}$$

3. **Berry ratio** (para. 2.106)

   $$\text{Berry ratio} = \frac{\text{Gross profit}}{\text{Operating expenses}}
   = \frac{\text{Net sales} - \text{COGS}}{\text{Operating expenses}}$$

   Interest and extraneous income are excluded from gross profit. Para. 2.107 sets three
   conditions for the ratio to be an appropriate indicator: the value of the function is
   proportional to operating expenses; it is not materially affected by the value of the
   product handled; and the tested party performs no other significant function that should
   be remunerated by another method. The same paragraph warns the ratio is **very sensitive
   to whether a cost is classified as an operating expense**.

   > **The OECD publishes no target range and no audit-trigger threshold for this ratio.**
   > A range comes from your own comparables set. Treat any "1.05–1.25" style figure quoted
   > without a comparables set behind it as a rule of thumb, not a standard.

4. **DEMPE-keyed profit split**

   $$\text{Key}_i = \sum_{f \in \{D,E,M,P,E\}} w_f \cdot s_{i,f}
   \qquad
   \text{Share}_i = \frac{\text{Key}_i}{\sum_j \text{Key}_j}$$

   $$\text{Allocation}_i = \text{Routine return}_i + \text{Share}_i \times
   \left(\text{Combined profit} - \textstyle\sum_j \text{Routine return}_j\right)$$

   With no routine returns this reduces to a **contribution analysis** (para. 2.150). With
   routine returns it is a **residual analysis** (para. 2.152).

   > **The OECD publishes no numeric DEMPE score and no formula converting one into a profit
   > share.** DEMPE (Ch. VI) is a functional analysis for deciding entitlement to intangible
   > return. The weights $w_f$ and scores $s_{i,f}$ are a taxpayer-constructed allocation key
   > and are governed by the profit-splitting-factor guidance: an economically valid basis
   > reflecting relative contributions (para. 2.166), optionally a weighting of multiple
   > factors (para. 2.170), determinable with reasonable reliability (para. 2.171). Costs are
   > an accepted proxy but may inadequately reflect intangible value (para. 2.171), and
   > self-developed intangibles generally do not appear on a balance sheet, so an asset-based
   > key needs a separate valuation (para. 2.175).

## 3. The two published fixed markups — and why a quant group usually cannot use them

| Regime | Markup | Applies to | Explicitly excluded |
| :--- | :--- | :--- | :--- |
| OECD simplified approach for low value-adding intra-group services (para. 7.61) | **5%** of relevant costs, no benchmarking study required | Supportive services that are not part of the group's core business | Core business services, **R&D (incl. software development outside the IT services of para. 7.49)**, manufacturing, purchasing of raw materials (para. 7.47) |
| US Services Cost Method, Treas. Reg. s.1.482-9(b) | **Cost, no markup** | Specified covered services and low-margin covered services (median comparable markup ≤ 7%) | **Research, development or experimentation; engineering or scientific; financial transactions incl. guarantees**; manufacturing; reselling/distribution; insurance (s.1.482-9(b)(4)) |
| IRAS routine support services concession (Singapore TP Guidelines, Annex C list) | **5%** cost markup | Listed routine support services, provided **only** to related parties, with all related costs in the base | Anything outside the Annex C list; strict pass-through cost pooling is charged at cost with no markup |

For a quantitative trading group the principal intercompany flows — strategy research,
model development, execution routing, and liquidity provision — land squarely in the
excluded categories of all three regimes. Genuine payroll, accounting and IT helpdesk
recharges may qualify; the alpha does not.

There is **no published general 5%–15% markup band**. Service markups commonly observed in
benchmarking studies fall in a broad range, but the arm's length figure for a given service
line comes from that line's comparables set, not from a remembered band.

## 4. Documentation and penalty exposure

**OECD BEPS Action 13 — three-tier documentation**
- **Master File**: group-wide organisational structure, business description, intangibles,
  intercompany financing, and financial/tax positions.
- **Local File**: the local entity's controlled transactions, the amounts, the method
  selected, and the benchmarking supporting it.
- **Country-by-Country Report**: required of MNE groups with annual consolidated group
  revenue **at or above EUR 750 million** (or near equivalent in domestic currency as of
  January 2015) in the immediately preceding fiscal year. Below that threshold no CbC report
  is due — Master File and Local File thresholds are set separately by each jurisdiction.

**United States**
- IRC s.482 gives the Secretary authority to allocate income among controlled entities.
- IRC s.6662(e)(1)(B): a **substantial** valuation misstatement — penalty **20%** of the
  underpayment (s.6662(a)) — where the net s.482 transfer price adjustment exceeds the
  lesser of **$5,000,000 or 10% of gross receipts**.
- IRC s.6662(h)(2)(A)(iii): a **gross** valuation misstatement — penalty **40%** — at the
  doubled thresholds of **$20,000,000 or 20% of gross receipts**.
- Treas. Reg. s.1.6662-6(d)(2)(iii): the penalty defence requires documentation in existence
  when the return is filed, produced to the IRS **within 30 days** of request, supported by
  the ten specified principal documents. Supplying the documents does not by itself preclude
  the penalty if the analysis is deficient.

**United Kingdom**
- TIOPA 2010 Part 4 applies the arm's length principle by reference to Article 9 of the OECD
  Model Tax Convention. Section 166 exempts most transactions of small and medium-sized
  enterprises.
- The Transfer Pricing Records Regulations 2023 (SI 2023/818) require UK members of groups
  meeting the CbCR threshold to keep a Master File and Local File for periods beginning on
  or after 1 April 2023, produced to HMRC within 30 days of request.

**Singapore**
- IRAS requires contemporaneous TP documentation from taxpayers above the prescribed gross
  revenue and transaction-value thresholds, prepared no later than the filing due date.

## Sources

- OECD, *Transfer Pricing Guidelines for Multinational Enterprises and Tax Administrations 2022* —
  paras. 2.106–2.108 (Berry ratio), 2.150 / 2.152 (contribution vs residual analysis),
  2.166–2.175 (profit splitting factors), 6.32 / 6.42 (DEMPE and legal ownership),
  7.34 / 7.47 / 7.61 (low value-adding intra-group services).
  <https://www.oecd.org/en/publications/oecd-transfer-pricing-guidelines-for-multinational-enterprises-and-tax-administrations-2022_0e655865-en.html>
- OECD, *Guidance on the Implementation of Country-by-Country Reporting, BEPS Action 13* (EUR 750m threshold).
  <https://www.oecd.org/content/dam/oecd/en/topics/policy-sub-issues/cbcr/guidance-on-the-implementation-of-country-by-country-reporting-beps-action-13.pdf>
- 26 U.S.C. s.6662 (subsections (a), (e), (h)). <https://www.law.cornell.edu/uscode/text/26/6662>
- 26 CFR s.1.482-9 — Methods to determine taxable income in connection with a controlled
  services transaction (Services Cost Method at (b)). <https://www.law.cornell.edu/cfr/text/26/1.482-9>
- IRS, *Transfer pricing documentation best practices FAQs* (Treas. Reg. s.1.6662-6 30-day rule).
  <https://www.irs.gov/businesses/international-businesses/transfer-pricing-documentation-best-practices-frequently-asked-questions>
- The Transfer Pricing Records Regulations 2023, SI 2023/818 (UK). <https://www.legislation.gov.uk/uksi/2023/818/made>
- IRAS, *Transfer Pricing Guidelines* e-Tax Guide (routine support services, Annex C).
  <https://www.iras.gov.sg/taxes/corporate-income-tax/specific-topics/transfer-pricing>
