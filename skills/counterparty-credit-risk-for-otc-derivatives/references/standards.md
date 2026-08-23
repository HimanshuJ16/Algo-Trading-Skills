# Standards for OTC Counterparty Risk

Primary sources (all consulted 2026-08-22):

- **BCBS 279** — Basel Committee on Banking Supervision, *The standardised
  approach for measuring counterparty credit risk exposures*, March 2014
  (rev. April 2014): https://www.bis.org/publ/bcbs279.pdf — consolidated
  into the Basel Framework as CRE52:
  https://www.bis.org/basel_framework/chapter/CRE/52.htm
- **US implementation** — Federal Register, *Standardized Approach for
  Calculating the Exposure Amount of Derivative Contracts*, final rule
  published 24 January 2020:
  https://www.federalregister.gov/documents/2020/01/24/2019-27249/standardized-approach-for-calculating-the-exposure-amount-of-derivative-contracts
- **ISDA CDS Standard Model** (40% recovery convention):
  https://www.cdsmodel.com/

| Metric | Engineering Standard | Source |
|---|---|---|
| Netting legal enforceability | Close-out netting MUST only be applied under a verified, legally enforceable ISDA Master Agreement (or equivalent with valid legal opinions across involved jurisdictions). SA-CCR recognises netting only under these legal-certainty conditions. | BCBS 279 paras 32-42, 134 |
| Replacement cost (margined) | RC = max(V − C, TH + MTA − NICA, 0); unmargined: RC = max(V − C, 0) | BCBS 279 paras 144, 136 |
| Definition of C | "C is the haircut value of net collateral held, which is calculated in accordance with the NICA methodology defined in paragraph 143" (para 136; para 144: "V and C are defined as in the unmargined formulation"). C therefore includes variation margin AND net independent collateral; NICA is additionally subtracted in the RC floor (para 145). | BCBS 279 paras 136, 143, 144, 145 |
| One-way margin direction | A one-way agreement in the *counterparty's* favour (bank posts but does not collect) MUST be treated as unmargined. This engine models the opposite direction (bank collects). | BCBS 279 para 138 |
| Margin call trigger | The delivery amount must be strictly positive as well as ≥ MTA. With MTA = 0 — the prescribed representation of an unmargined set — a bare `≥ MTA` test raises a $0 call on any set at or below its threshold. | ISDA CSA "Delivery Amount"; BCBS 279 para 140 fn 9 |
| EAD multiplier | EAD = alpha × (RC + PFE) with alpha = 1.4 (mandatory, not optional conservatism) | BCBS 279 para 128; US final rule Jan 2020 |
| PFE multiplier | min(1, 0.05 + 0.95·exp((V − C)/(2·0.95·AddOn))) | BCBS 279 para 149 |
| SA-CCR supervisory factors | Interest rate 0.50% · FX 4.0% · equity single-name 32% · equity index 20% · commodity 18% (oil/gas, metals, agricultural, other; electricity 40%) · credit index IG 0.38% / SG 1.06% · credit single-name AAA 0.38%, AA 0.38%, A 0.42%, BBB 0.54%, BB 1.06%, B 1.6%, CCC 6.0%. Basis transactions: SF × 0.5; volatility transactions: SF × 5 (para 184). Factors MUST be sourced from this table, never from memory. | BCBS 279 Table 2 (para 183), para 184 |
| Recovery rate assumption | R = 40% is the ISDA CDS Standard Model convention for senior unsecured corporate reference entities — a pricing convention, not a regulatory mandate. Subordinated exposures use lower assumptions. | ISDA CDS Standard Model (cdsmodel.com) |
| CVA measure | Canonical CVA prices default risk as (1−R)·Σ EE(t)·PD(t)·DF(t) over time buckets (Basel CVA framework, MAR50). This engine's (1−R)·EAD·PD is a single-period undiscounted proxy; regulatory CVA capital uses BA-CVA/SA-CVA. | BIS MAR50; OSFI CAR Ch. 8 |

**Known simplifications of this engine (acceptable for limit monitoring,
NOT for regulatory reporting):** the add-on aggregate omits adjusted
notionals, supervisory deltas, maturity factors, and hedging-set
correlation aggregation (BCBS 279 paras 151-184); the para 129 cap of
margined EAD at unmargined EAD is consequently not applied; collateral is
taken without haircuts; the CSA is one-way in the bank's favour, so only
the ISDA CSA Delivery Amount is computed and the Return Amount owed on an
over-collateralised set is not; and the para 185 requirement to split a
netting set covered by multiple margin agreements into sub-netting sets is
left to the caller.
