# Standards for Credit Card Data Signal Construction

Primary sources (all consulted 2026-08-22):

- **NRF 4-5-4 Calendar** (National Retail Federation): https://nrf.com/resources/4-5-4-calendar
- **SEC Code of Ethics Risk Alert** (US SEC, Division of Examinations): https://www.sec.gov/files/code-ethics-risk-alert.pdf
- **WSJ**: "How Credit-Card Data Might Be Distorting Retail Stocks" (2017): https://www.wsj.com/articles/how-credit-card-data-might-be-distorting-retail-stocks-1483468912
- **Facteus buyer's guide** (vendor lag spectrum): https://facteus.com/buying-guides/the-ultimate-guide-to-debit-and-credit-card-transaction-data

| Metric | Engineering Standard | Source |
|---|---|---|
| Availability lag handling | Backtests MUST use the vendor's documented delivery lag and as-delivered (point-in-time) snapshots — lags range from ~12 hours to 5-7 days/weekly by vendor and product tier, so no universal constant applies. Panels restate as late postings settle; final restated values must not be backtested as if available on first delivery. | Facteus buyer's guide; vendor documentation |
| Consensus source | Consensus revenue estimates MUST be point-in-time (e.g. IBES point-in-time, Bloomberg/FactSet as-of consensus). Restated consensus leaks information. | Point-in-time consensus practice (IBES PIT; Eagle Alpha, "Importance of Point-in-Time for Alternative Data", 2024) |
| Signal threshold | $\pm 2.5\%$ is a tunable default, not a validated constant: calibrate the threshold to the panel's measured historical prediction error before trading directional signals. | Engineering default (no external standard exists) |
| Seasonality alignment | YoY comparisons MUST use seasonality-aligned fiscal quarters; for US retailers align on the NRF 4-5-4 calendar and restate 53-week years for comparability. | NRF 4-5-4 Calendar |
| Panel shift detection | Panel composition shifts MUST be diagnosed (ticket/volume decomposition) before interpreting spend growth as demand; card panels have demonstrably distorted retail-stock expectations through coverage artifacts. | WSJ (2017) |
| Compliance (US) | Consumer transaction data usage falls under adviser MNPI procedures: maintain documented vendor due diligence (aggregation/de-identification representations) and written policies per Advisers Act Rule 204A. SEC exam staff has flagged advisers using alternative data without such procedures. | SEC Code of Ethics Risk Alert (US jurisdiction; other jurisdictions have analogues) |
| Confidence semantics | `confidence_score` is an uncalibrated heuristic rank; treat as a sorting aid only, never as a probability for sizing. | Engine docstring |
