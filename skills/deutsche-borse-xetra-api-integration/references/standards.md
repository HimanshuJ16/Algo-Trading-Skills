# Standards for Deutsche Börse Xetra API Integration

| Metric | Engineering Standard |
|---|---|
| Xetra Tick Compliance | ALL Xetra order prices MUST be exact multiples of the active price-band tick size rule. |
| MiFID II Account Tagging | ALL orders MUST specify valid `account_type` (`P`, `A`, `M`) and DEA `mifid_short_code`. |
| Protocol Interface | High-speed order submission MUST utilize T7 ETI (Enhanced Trading Interface). |
