# Standards for Short-Selling Borrow Cost and Availability Modeling

| Metric | Standard Value | Description |
|---|---|---|
| GC Borrow Rate | $\approx 0.25\% - 0.50\%$ annualized | Applied to liquid, highly available stocks. |
| HTB Utilization Threshold | $> 80\%$ | Triggers dynamic HTB borrow rate scaling. |
| Daily Fee Formula | $\text{Notional} \times \frac{\text{Rate}}{365} \times \text{Days}$ | Standard 365-day annualization drag. |
