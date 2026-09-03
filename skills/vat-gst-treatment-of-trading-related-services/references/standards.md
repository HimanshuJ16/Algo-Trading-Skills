# Institutional VAT/GST Standards for Trading-Related Services

All rates and rules below were verified current as at September 2026. Rates
change; re-verify against the tax authority before a filing relies on them.

## 1. Global Tax Jurisdiction VAT/GST Standard Rates
| Jurisdiction Code | Tax Authority | Standard VAT/GST Rate | Reverse charge on imported B2B services |
| :--- | :--- | :--- | :--- |
| **UK_HMRC** | UK HM Revenue & Customs (VATA 1994) | **20.0%** | Unconditional — VATA 1994 s.8 (place of supply s.7A) |
| **EU_GERMANY** | Bundeszentralamt für Steuern (UStG) | **19.0%** | Unconditional — Directive 2006/112/EC Art 196 (place of supply Art 44) |
| **EU_IRELAND** | Revenue Commissioners | **23.0%** | Unconditional — Directive 2006/112/EC Art 196 |
| **SINGAPORE_IRAS** | Inland Revenue Authority of Singapore (GST) | **9.0%** (since 1 Jan 2024) | **Conditional** — only where the recipient is GST-registered and *not* entitled to full input tax credit (or is non-registered and exceeds the S$1m imported-services threshold) |
| **AUSTRALIA_ATO** | Australian Taxation Office (GST) | **10.0%** | **Conditional** — GST Act 1999 Div 84 (s.84-5, s.84-10): recipient registered *and* the acquisition is not solely for a creditable purpose |
| **US_NON_VAT** | US state sales/use tax regimes | **n/a (out of scope)** | No federal VAT system. State and local sales/use tax is **not** modelled by this skill and may still apply. |

> The unconditional/conditional split matters. A fully-recovering Singapore or
> Australian entity is outside its reverse charge regime entirely; a UK or EU
> entity is inside it regardless of recovery position. The engine models this
> using a 100% recovery ratio as the proxy for full entitlement — a proxy, not
> the statutory test.

---

## 2. Trading-Related Expense VAT/GST Classification Standard
| Service Category | Standard Tax Treatment | Statutory / guidance basis | Input Tax Recovery Eligibility |
| :--- | :--- | :--- | :--- |
| **Exchange Execution Fees** | **EXEMPT** where the charge is for intermediation | UK VATA 1994 Sch 9 Grp 5 / EU Art 135(1)(f); HMRC VAT Notice 701/49 para 9.1 | Nil, unless a specified supply (§5) |
| **Clearing Fees** | **EXEMPT** | HMRC VAT Notice 701/49 para 2.8 ("A service supplied by a clearing-house for settling indebtedness between members is an exempt supply") / EU Art 135(1)(d) | Nil, unless a specified supply (§5) |
| **Brokerage Commissions** | **EXEMPT** financial intermediation | UK VATA 1994 Sch 9 Grp 5 item 5; HMRC VAT Notice 701/49 para 9.1 | Nil, unless a specified supply (§5) |
| **Exchange Membership & Connectivity Fees** | **STANDARD-RATED** | HMRC VAT Notice 701/49 para 6.9: "Basic admission or membership charges are taxable at the standard rate… The liability of other charges depends on exactly what is being done by the exchange for the charge." | Partial exemption pro-rata % |
| **Co-Location & Data Feeds** | **STANDARD-RATED** | IT infrastructure / electronically supplied services; see §6 on the immovable-property risk | Partial exemption pro-rata % |
| **Software Licensing** | **STANDARD-RATED** | Electronically supplied services | Partial exemption pro-rata % |
| **Management & Advisory Fees** | **STANDARD-RATED** | Professional services | Partial exemption pro-rata % |

> **An exchange invoice is not a single supply.** Execution/intermediation may
> be exempt while membership, port, connectivity, technology and market-data
> lines on the same invoice are standard-rated. Treating the whole invoice as
> exempt under-recovers input tax and misstates the return. Ireland's Revenue
> Commissioners and HMRC do not align on every fee type — resolve the split per
> jurisdiction and per fee, not per vendor.

---

## 3. Partial Exemption Pro-Rata Recovery
A trading entity making both exempt supplies (financial intermediation, trading
income) and taxable supplies (management recharges, advisory) recovers input VAT
on residual overhead using the pro-rata recovery ratio.

**Standard method** — UK VAT Regulations 1995 (SI 1995/2518) reg 101(2)(d);
EU Directive 2006/112/EC Art 174(1):

$$\text{Recovery Ratio \%} = \left( \frac{\text{Taxable Supplies (turnover, ex-VAT)}}{\text{Taxable Supplies} + \text{Exempt Supplies}} \right) \times 100$$

**Statutory rounding.** The percentage is **rounded up**, which the raw ratio
does not do:

| Rule | Treatment | Basis |
| :--- | :--- | :--- |
| UK, residual input tax ≤ £400,000/month avg | Round **up to the next whole number** (49.1% → 50%) | VAT Regs 1995 reg 101(4) |
| UK, residual input tax > £400,000/month avg | Round **up to 2 decimal places** | VAT Regs 1995 reg 101(5) |
| EU | Round **up to a figure not exceeding the next whole number** | Directive 2006/112/EC Art 175(1) |
| Singapore, Australia | No equivalent statutory rounding-up rule | — |

Skipping the rounding-up systematically **understates** recoverable input VAT
for UK and EU entities.

**Input tax formulae:**

$$\text{Input VAT Paid} = \text{Net Amount} \times \frac{\text{VAT Rate \%}}{100}$$

$$\text{Recoverable Input VAT} = \text{Input VAT Paid} \times \frac{\text{Recovery Ratio \%}}{100}$$

$$\text{Unrecoverable VAT Expense} = \text{Input VAT Paid} - \text{Recoverable Input VAT}$$

---

## 4. Cross-Border Reverse Charge Mechanism (RCM) Standard
Where a UK/EU/SG/AU trading entity receives a standard-rated service from a
foreign supplier and the reverse charge applies (§1):

1. The supplier issues the invoice with **no VAT** (out of scope for the supplier).
2. The recipient self-assesses **output VAT** at its own domestic standard rate:
   $\text{Output VAT} = \text{Net Amount} \times \frac{\text{Domestic Standard Rate \%}}{100}$ (UK return **Box 1**).
3. The recipient claims the corresponding **input VAT**, restricted by the
   partial exemption recovery ratio (UK return **Box 4**).
4. Net cost to the entity:
   $\text{Unrecoverable VAT} = \text{RCM VAT} \times \left(1 - \frac{\text{Recovery Ratio \%}}{100}\right)$

Both legs must appear on the return. Reporting only the net effect omits the
Box 1 output tax and understates declared turnover.

An **exempt** service received from abroad carries no reverse charge — the
deemed supply under VATA 1994 s.8 takes the liability it would have had if
supplied domestically.

---

## 5. Input Tax Recovery on Exempt Financial Supplies to Non-UK Customers
Exempt supplies falling within VATA 1994 Sch 9 Grp 5 items 1–6 and 8, made to a
recipient established **outside the UK**, are "specified supplies" that carry a
right of input tax deduction under the VAT (Input Tax) (Specified Supplies)
Order 1999 (SI 1999/3121), made under VATA 1994 s.26(2)(c). They behave like
zero-rated supplies for recovery purposes.

**Consequence for the ratio:** specified supplies belong in the **numerator**
(taxable supplies) of §3, not in the exempt denominator-only bucket. Classifying
all exempt financial income as non-recoverable understates the recovery ratio.

---

## 6. Co-Location: Immovable Property Risk
CJEU **C-215/19 (A Oy), 2 July 2020** held that data-centre colocation was
neither an exempt letting of immovable property nor a service connected with
immovable property, because the customers had **no exclusive right of use** of a
defined part of the building and the equipment cabinets were not permanently
installed.

The corollary matters for trading firms: where a contract **does** grant
exclusive use of a defined space — a dedicated cage or suite at LD4, FR2, NY4 —
the supply can fall under the immovable-property rule (Directive Art 47; VATA
1994 Sch 4A para 1) and be taxable **where the data centre is located**, which
means local VAT registration rather than a reverse charge at the customer's
location. Read the colocation contract before assuming the general B2B rule.

---

## 7. Out of Scope for This Skill
- **Currency.** VAT/GST is declared in the currency of the tax jurisdiction
  (GBP, EUR, SGD, AUD). No FX conversion is performed; amounts must be
  pre-converted at the correct statutory rate and date before ingestion.
- **UK de minimis (VAT Regs 1995 reg 106).** Where exempt input tax is both
  ≤ £625/month on average and ≤ 50% of total input tax, *all* input tax is
  recoverable. Institutional trading entities are ordinarily far above this,
  so it is not modelled — verify rather than assume.
- **Australian apportionment method.** The ATO does not prescribe the EU/UK
  turnover pro-rata. GSTR 2006/3 requires a method that is "fair and
  reasonable", with direct estimation preferred. Applying the turnover ratio to
  an Australian entity needs its own justification, as does the reduced input
  tax credit regime for financial supplies.
- **Special/sectoral methods (PESM), annual adjustments, capital goods scheme,
  credit notes and refunds, and VAT groups.**
- **US state and local sales/use tax.**

## Sources
- HMRC, *VAT Notice 701/49: finance* — paras 2.8, 6.9, 9.1. https://www.gov.uk/government/publications/vat-notice-70149-finance/vat-notice-70149-finance
- HMRC, *VAT Partial Exemption Guidance* PE30500 (standard method, reg 101 rounding). https://www.gov.uk/hmrc-internal-manuals/vat-partial-exemption-guidance/pe30500
- The Value Added Tax Regulations 1995 (SI 1995/2518), regs 101, 106. https://www.legislation.gov.uk/uksi/1995/2518/part/XIV/made
- The Value Added Tax (Input Tax) (Specified Supplies) Order 1999 (SI 1999/3121). https://www.legislation.gov.uk/uksi/1999/3121/made
- Council Directive 2006/112/EC, Arts 44, 47, 135(1), 173–175, 196. https://eur-lex.europa.eu/legal-content/EN/ALL/?uri=celex%3A32006L0112
- CJEU, Case C-215/19 *Veronsaajien oikeudenvalvontayksikkö (A Oy)*, 2 July 2020.
- IRAS, *Local businesses importing services and importing or supplying low-value goods*. https://www.iras.gov.sg/taxes/goods-services-tax-(gst)/gst-and-digital-economy/local-businesses
- ATO, *Reverse charge GST on offshore goods and services purchases*; A New Tax System (Goods and Services Tax) Act 1999 ss.84-5, 84-10. https://classic.austlii.edu.au/au/legis/cth/consol_act/antsasta1999402/s84.10.html
- ATO, *GSTR 2006/3* (apportionment for providers of financial supplies). https://www.ato.gov.au/law/view/document?docid=GST/GSTR20063/NAT/ATO/00001
- Revenue Commissioners (Ireland), *VAT Treatment of Stock Exchange Fees* (Tax and Duty Manual). https://www.revenue.ie/en/tax-professionals/tdm/value-added-tax/part03-taxable-transactions-goods-ica-services/Financial-Services/services-stock-exchange-fees.pdf
