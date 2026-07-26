# Standards for CFTC 4.13(a)(3) Compliance

| Metric | Engineering Standard |
|---|---|
| Testing Requirement | The exemption must be satisfied "at all times" a new position is established. EOD (End of Day) checks are insufficient for active intraday trading. |
| In-The-Money Options | When calculating the premium for the 5% margin test, the algorithm MUST subtract the in-the-money amount of the option at the time of purchase, as permitted by CFTC rules. |
| Notional Value Netting | Notional value calculation may allow netting of futures contracts with the exact same underlying and maturity across different accounts, but gross notional is generally required otherwise. Our engine defaults to gross notional for safety. |