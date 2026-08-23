# Standards for Cross-Strategy Tax Lot Optimization

**Jurisdiction: United States, federal.** Every rule below is US federal tax law. State treatment may differ, and no non-US regime is modelled here.

## 1. Authority matrix

| Area | Authority | Requirement | Consequence of non-compliance |
| :--- | :--- | :--- | :--- |
| Default lot matching | Treas. Reg. § 1.1012-1(c)(1)(i) | Absent an adequate identification, the sale is charged against the **earliest lot acquired** (FIFO). | HIFO/Specific ID selection is disregarded; reported basis reverts to FIFO. |
| Adequate identification deadline | Treas. Reg. § 1.1012-1(c)(8) | Identification must be made no later than the **earlier of the settlement date or the settlement time required by Exchange Act Rule 15c6-1**. A standing order or instruction counts as identification made at the time of sale. | Internal HIFO ledger diverges from the broker's 1099-B. |
| Standard settlement cycle | 17 CFR § 240.15c6-1, as amended (compliance date 28 May 2024) | **T+1** for most broker-dealer transactions in US securities. Exempted securities, government securities, municipal securities, commercial paper, bankers' acceptances and commercial bills are outside Rule 15c6-1(a). | The identification window is one business day, not two. |
| Holding-period test | IRC § 1222 | Long-term requires holding for **more than one year**; the period begins the day after acquisition and includes the disposition date. | Short-term gains taxed at ordinary rates are misreported as long-term. |
| Wash sale window | IRC § 1091(a) | 61 days: `[t_loss - 30, t_loss + 30]`, inclusive of the sale date. Applies to substantially identical stock or securities. | Loss deduction disallowed on audit. |
| Partial replacement | IRC § 1091(b) | Where fewer replacement shares are acquired than loss shares sold, the disallowance is limited to the shares actually replaced. | Over- or under-statement of allowable capital loss. |
| Basis adjustment | IRC § 1091(d) | The disallowed loss is added to the basis of the replacement shares. | Loss permanently lost rather than deferred. |
| Holding-period tacking | IRC § 1223(3) | The loss lot's holding period tacks onto the replacement lot. | Long-term qualification lost on the replacement shares. |
| Mark-to-market election | IRC § 475(f); IRS Topic 429 | For an elected trading business, gains/losses are ordinary and **the wash-sale rules and capital-loss limitations do not apply**. Securities segregated as investments on the day of acquisition remain subject to the ordinary rules. | A disallowance is computed where none exists. |

## 2. Scope of this module

Implemented here:

- Lot ordering: HIFO, LTCG-preferring, and statutory FIFO by acquisition date.
- Holding-period classification, calendar-accurate when a `sale_date` is supplied.
- Internal cross-strategy netting, with the crossed quantity excluded from disposition.
- A § 1091 **screen**: window membership and a quantity-limited disallowance figure.

Deliberately **not** implemented here — use `wash-sale-rule-tracking-us`:

- The § 1091(d) basis adjustment on replacement lots.
- § 1223(3) holding-period tacking.
- Cross-account and IRA aggregation (the rule applies per taxpayer across all accounts, and a loss replaced inside an IRA is disallowed with no basis relief).
- Option-to-stock and other "substantially identical" determinations. This module matches on ticker only; substantial identity is a legal judgement it does not make.

## 3. Equations

Realized result per lot, for `q` shares at price `P_exec` against basis `P_basis`:

$$\text{PnL} = q \times (P_{\text{exec}} - P_{\text{basis}})$$

Wash-sale disallowance, where `R` is the total replacement shares acquired in the 61-day window (IRC § 1091(b)):

$$\text{Matched} = \min(q_{\text{loss}}, R_{\text{remaining}}) \qquad \text{Disallowed} = \text{Matched} \times |P_{\text{exec}} - P_{\text{basis}}|$$

$$\text{Net deductible} = \text{PnL}_{\text{total}} + \text{Disallowed}_{\text{total}}$$

Cross-strategy netting for symbol `S`:

$$\text{Crossed} = \min\left(\textstyle\sum Q_{\text{buy}},\ \sum Q_{\text{sell}}\right) \qquad \text{Net} = \textstyle\sum Q_{\text{buy}} - \sum Q_{\text{sell}}$$

Within one tax entity the crossed quantity is a book transfer and realizes nothing; only `|Net|` is a disposition subject to lot selection.

## 4. Sources

- [26 CFR § 1.1012-1 — Basis of property](https://www.law.cornell.edu/cfr/text/26/1.1012-1) (paragraphs (c)(1)(i) and (c)(8)).
- [SEC, Shortening the Securities Transaction Settlement Cycle — T+1 FAQ](https://www.sec.gov/exams/educationhelpguidesfaqs/t1-faq) (compliance date 28 May 2024).
- [SEC Release 34-96930, final rule amending Rule 15c6-1](https://www.sec.gov/files/rules/final/2023/34-96930.pdf).
- [IRS Topic No. 429, Traders in Securities](https://www.irs.gov/taxtopics/tc429) (§ 475(f) election; wash-sale rules do not apply to the elected trading business).
