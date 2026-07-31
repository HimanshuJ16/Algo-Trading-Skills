# Standards for Security Symbology Validation

| Identifier | Length | Validation Standard |
|---|---|---|
| ISIN | 12 Chars | MUST pass Modulo 10 Luhn double-add-double checksum validation. |
| CUSIP | 9 Chars | MUST pass 9th character Modulo 10 checksum validation. |
| SEDOL | 7 Chars | MUST pass weighted Modulo 10 checksum ($1, 3, 1, 7, 3, 9, 1$). |
