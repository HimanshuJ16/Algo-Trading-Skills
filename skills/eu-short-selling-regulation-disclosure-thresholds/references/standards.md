# Standards — eu-short-selling-regulation-disclosure-thresholds

## What is actually mandated, and by whom

Every row was checked against the source named in it. Jurisdiction is the EU/EEA
throughout — none of this is authority for a UK, Swiss, US or APAC short-selling
obligation.

| Requirement | Source | What it actually says | Status |
|---|---|---|---|
| Private notification to the relevant competent authority at 0.1% and each 0.1% above | **Regulation (EU) No 236/2012 Art. 5(2)**, as amended by **Commission Delegated Regulation (EU) 2022/27** | The relevant notification threshold is 0.1% of the issued share capital of the company concerned and each 0.1% above that. The original 0.2% figure was permanently lowered to 0.1%; the amendment applies from **31 January 2022**. | Mandatory, EU |
| Public disclosure at 0.5% and each 0.1% above | **Art. 6(2)** | The relevant publication threshold is 0.5% of the issued share capital and each 0.1% above that. ESMA states the same on its Short Selling page. | Mandatory, EU |
| Relevant time is midnight at the end of the trading day | **Art. 9(2)** | The position is calculated at midnight at the end of the trading day on which the person holds it. Intraday peaks are not the reported figure. | Mandatory, EU |
| Filing by 15.30 on the following trading day | **Art. 9(2)** | The notification or disclosure is made not later than 15.30 on the following trading day. | Mandatory, EU |
| That 15.30 is **local time in the Member State of the relevant competent authority** | **Art. 9(3)**, as read by **ESMA Q&A A5.2** and **A9.3** | A5.2: "The time specified in Article 9(2) of the Regulation for the notification (i.e. not later than 15:30h of the following trading day) is the one of the Member State of the relevant competent authority (RCA) … the trading days would be the one of the Member State of the RCA." A9.3 refers to "15.30h local time". | Mandatory, EU — **not** a Union-wide CET instant |
| The reported percentage is truncated, not rounded, to two decimals | **ESMA Q&A A5.6** | "the position to report should be rounded to the first two decimal places by truncating the other decimal places." Worked example: 0.3199% is notified as 0.31%; 0.1987% did not reach the (then 0.2%) threshold. | ESMA guidance, EU |
| Notification is due on reaching, exceeding **or falling below** a threshold; nothing is due inside an already-notified band | **Art. 5(2)/6(2)**, **ESMA Q&A A5.7** | "A notification is required where the position reaches, exceeds or falls below a relevant notification threshold … If there is a change of net short position which remains within the relevant notification threshold, for which a notification has already been made, there is no requirement for a further notification." | Mandatory, EU |
| Net short position is calculated on a delta-adjusted basis | **Commission Delegated Regulation (EU) No 918/2012 Annex II Part 1** | Any derivative and cash position is accounted for on a delta-adjusted basis, cash having delta 1; the net short position is obtained by netting long and short delta-adjusted positions in a given issuer. | Mandatory, EU |
| Issued share capital covers all classes, including preference and non-voting | **Art. 2(1)(l)**, **ESMA Q&A A6.6** | "All classes of issued shares should be considered in the calculation of the net short position (both numerator and denominator) irrespective of their characteristics (common stock, preferred, saving, etc.)". | Mandatory, EU |
| ETF and depositary-receipt exposure counts towards the position | **ESMA Q&A A4.6, A4.7** | GDRs/ADRs and ETFs are taken into account when calculating the net short position under Arts. 5 and 6, through look-through to the underlying shares. | ESMA guidance, EU |
| Uncovered short sales of shares prohibited | **Art. 12(1)(a)-(c)** | A short sale of a share may be entered into only where the person has borrowed the share, has entered into an agreement to borrow it, or has an arrangement with a third party confirming the share has been located and has taken measures giving a reasonable expectation that settlement can be effected when due. | Mandatory, EU |
| Locate arrangements must be of a specified type and evidenced in a durable medium | **Commission Implementing Regulation (EU) No 827/2012 Arts. 5-7** | Art. 6 sets out three categories — standard locate arrangements, standard same-day locate arrangements, and easy-to-borrow-or-purchase arrangements for liquid/main-index shares. Art. 7 requires the arrangements, confirmations and instructions to be provided in a durable medium as evidence. ESMA has stated that pointing at an easy-to-borrow list does not by itself satisfy Art. 6. | Mandatory, EU |
| ETFs and depositary receipts are **not** shares for Art. 12 | **ESMA Q&A A4.6, A4.7** | "GDR and ADR are not shares for the purpose of Article 12"; "ETFs are not per se subject to Article 12". | ESMA guidance, EU |
| Shares whose principal trading venue is in a third country are out of scope | **Art. 16**, **ESMA Q&A A4.4, A4.5** | Both conditions must hold for the regime to apply: admitted to trading on a Union venue *and* principal trading venue in the Union. A4.4's worked example — a US company admitted in Germany with its principal venue in the USA — is exempt from Arts. 5, 6, 12 and 15. ESMA publishes the exempted-shares list. | Mandatory, EU |
| Market making and primary market operations exempt on 30 calendar days' notice | **Art. 17** | The exemption applies only where the person has notified the competent authority of its home Member State in writing, not less than 30 calendar days before first intending to use it; the authority may object or prohibit within that period. | Mandatory, EU |

## Out of scope for this skill — do not read these rows into it

| Regime | Why it is different |
|---|---|
| Sovereign debt net short positions (**Art. 7**) | Thresholds are absolute amounts set per sovereign issuer and published by ESMA (Delegated Regulation (EU) No 918/2012 Art. 21 and Annex), not percentages of issued share capital, and positions are duration-adjusted. There is no public-disclosure tier. |
| Uncovered sovereign CDS (**Art. 14**) | A separate restriction with its own correlation and hedging tests (DR 918/2012 Arts. 14-18). |
| Emergency intervention powers (**Arts. 18-23, 28**) | NCAs and ESMA may impose temporary bans and lower thresholds instrument-by-instrument. The engine's thresholds are configurable for exactly this reason; the defaults are the standing Arts. 5(2)/6(2) figures, not an emergency measure. |
| UK, Swiss and other non-EEA regimes | Separate legal instruments, separate thresholds, separate forms. |

## Currency of the sources

- The **0.1% notification threshold has been in force since 31 January 2022** (Commission Delegated Regulation (EU) 2022/27). ESMA's Short Selling page states 0.1% and each 0.1% above, and 0.5% for publication, as the current position; the AMF states the same and dates the change to 31 January 2022.
- The ESMA SSR Q&A document cited here (**ESMA70-145-408**) carries the note that it is not updated after 31 December 2023. Its answers on rounding (A5.6) and on which threshold changes are notifiable (A5.7) use the pre-2022 **0.2%** figure in their worked examples. The *methods* they state — truncation to two decimals, band-based notification, notification on falling below — are unaffected; only the numbers in the examples are historical. This file quotes the methods and applies them to the current thresholds.
- ESMA has published a final report on the review of the SSR and consulted further during 2025 on, among other things, the calculation and publication of net short positions. **No amendment to the 0.1%/0.5% thresholds arising from that review had been adopted as of this audit (August 2026)** — do not pre-implement a proposal.

## Not found — do not claim it

- **No source sets "15:30 CET" as a Union-wide deadline.** Art. 9 and ESMA Q&A A5.2 both point to the local time of the RCA's Member State. Any code, runbook or scheduler stating CET is asserting something the regulation does not.
- **No regulator publishes a trading-day calendar inside the SSR texts.** The following-trading-day calculation depends on the RCA Member State's own calendar; the firm must supply it. `next_weekday_excluding_holidays` in `scripts/` is a weekends-only stopgap and is named to make that obvious.
- **There is no de minimis issuer-size carve-out** from Arts. 5/6 in the Regulation.

## Primary sources

| Instrument | Reference |
|---|---|
| Short Selling Regulation | Regulation (EU) No 236/2012 of the European Parliament and of the Council of 14 March 2012 on short selling and certain aspects of credit default swaps, OJ L 86, 24.3.2012 |
| Notification threshold amendment | Commission Delegated Regulation (EU) 2022/27 of 27 September 2021 amending Regulation (EU) No 236/2012 as regards the adjustment of the relevant notification threshold, applicable from 31 January 2022 |
| Calculation of net short positions | Commission Delegated Regulation (EU) No 918/2012 of 5 July 2012 (definitions, calculation of net short positions, covered sovereign CDS, notification thresholds), Annex II Part 1 |
| Locate arrangements and disclosure means | Commission Implementing Regulation (EU) No 827/2012 of 29 June 2012, Arts. 5-7 |
| ESMA guidance | ESMA, *Questions and Answers on the implementation of the Regulation on short selling and certain aspects of credit default swaps*, ESMA70-145-408 (A4.4-A4.9, A5.2, A5.6, A5.7, A6.6, A9.3, section 10) |
| ESMA current thresholds and registers | ESMA, *Short Selling* — https://www.esma.europa.eu/esmas-activities/markets-and-infrastructure/short-selling (exempted-shares list, sovereign thresholds, current 0.1%/0.5% figures) |
