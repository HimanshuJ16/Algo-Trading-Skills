# Standards for Historical Order Book Reconstruction

| Metric | Engineering Standard |
|---|---|
| Order Lookup Complexity | Order ID map lookups MUST execute in $O(1)$ time complexity. |
| BBO Sorting | Bids MUST be sorted descending; Asks MUST be sorted ascending. |
| Book Integrity | Reconstructed books MUST trigger alerts if Best Bid $\ge$ Best Ask (Crossed Book). |
