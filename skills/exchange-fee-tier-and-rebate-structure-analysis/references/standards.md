# Standards for Exchange Fee Tier and Rebate Analysis

| Metric | Engineering Standard |
|---|---|
| Maker-Taker Netting | Net transaction costs MUST subtract maker rebates from gross taker fees. |
| Inverted Venue Logic | Inverted venues (Taker-Maker) MUST charge maker fees and credit taker rebates. |
| Tier Gap Precision | Volume gaps to next tier MUST be tracked daily to optimize month-end routing. |
