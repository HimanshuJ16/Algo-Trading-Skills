# Standards for Patent Filing Data for Innovation Signal Research

| Metric | Engineering Standard |
|---|---|
| Forward Citation Scaling | MUST use logarithmic scaling: $C = \ln(1 + \text{ForwardCitations})$. |
| Z-Score Bounds | Normalized signals MUST be winsorized to $[-3.0, +3.0]$. |
| Availability Lag | Application-to-grant lag MUST be modeled ($\ge 18$ months). |