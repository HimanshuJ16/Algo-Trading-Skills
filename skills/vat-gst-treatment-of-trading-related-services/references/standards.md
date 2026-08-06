# Institutional VAT/GST Standards for Trading-Related Services

## 1. Global Tax Jurisdiction VAT/GST Standard Rates
| Jurisdiction Code | Tax Authority | Standard VAT/GST Rate | Cross-Border Reverse Charge Mandatory? |
| :--- | :--- | :--- | :--- |
| **UK_HMRC** | UK HM Revenue & Customs (VATA 1994) | **20.0%** | Yes (VATA 1994 s.7A) |
| **EU_GERMANY** | Germany Bundeszentralamt für Steuern (UStG) | **19.0%** | Yes (EU Directive Art 196) |
| **EU_IRELAND** | Ireland Revenue Commissioners | **23.0%** | Yes (EU Directive Art 196) |
| **SINGAPORE_IRAS** | Inland Revenue Authority of Singapore (GST) | **9.0%** | Yes (Reverse Charge Regime for Imported Services) |
| **AUSTRALIA_ATO** | Australian Taxation Office (GST) | **10.0%** | Yes (Reverse Charge Division 84) |
| **US_NON_VAT** | US State Sales Tax Regimes | **0.0% (Out of Scope)** | No (No Federal VAT System) |

---

## 2. Trading-Related Expense VAT/GST Classification Standard
| Service Category | Standard Tax Treatment | Statutory Exemption Basis | Input Tax Recovery Eligibility |
| :--- | :--- | :--- | :--- |
| **Exchange Execution Fees** | **EXEMPT** | UK VATA 1994 Sch 9 Grp 5 / EU Art 135(1)(f) | 0% (Unrecoverable Input Tax) |
| **Clearing Fees** | **EXEMPT** | UK VATA 1994 Sch 9 Grp 5 / EU Art 135(1)(d) | 0% (Unrecoverable Input Tax) |
| **Brokerage Commissions** | **EXEMPT** | Financial Intermediation Exemption | 0% (Unrecoverable Input Tax) |
| **Co-Location & Data Feeds** | **STANDARD-RATED** | IT Infrastructure / Telecommunications | Partial Exemption Pro-Rata % |
| **Software Licensing** | **STANDARD-RATED** | Electronic Software Supply | Partial Exemption Pro-Rata % |
| **Management & Advisory Fees**| **STANDARD-RATED** | Professional Services | Partial Exemption Pro-Rata % |

---

## 3. Mathematical Partial Exemption Recovery Math (PESM)
Because trading entities make both **Exempt Supplies** (trading gains, financial intermediation) and **Taxable Supplies** (management recharges, advisory), input VAT paid on general overhead is recovered using the **Pro-Rata Partial Exemption Recovery Ratio**:

$$\text{Recovery Ratio \%} = \left( \frac{\text{Taxable Supplies (Turnover)}}{\text{Taxable Supplies} + \text{Exempt Supplies}} \right) \times 100$$

### Unrecoverable Input VAT Expense Formula:
$$\text{Input VAT Paid} = \text{Net Amount} \times \text{VAT Rate}$$

$$\text{Recoverable Input VAT} = \text{Input VAT Paid} \times \left( \frac{\text{Recovery Ratio \%}}{100} \right)$$

$$\text{Unrecoverable VAT Expense} = \text{Input VAT Paid} - \text{Recoverable Input VAT}$$

---

## 4. Cross-Border Reverse Charge Mechanism (RCM) Standard
When a UK/EU/SG trading entity receives standard-rated services from a foreign vendor:
1. Vendor issues invoice at **0% VAT** (Zero-Rated / Out of Scope for vendor).
2. Recipient self-assesses **Output VAT**: $\text{Output VAT} = \text{Net Amount} \times \text{Domestic Standard Rate}$.
3. Recipient self-assesses **Input VAT**: Subject to Partial Exemption recovery ratio.
4. Net Tax Effect: $\text{Unrecoverable VAT} = \text{RCM VAT} \times (1 - \text{Recovery Ratio \%})$.