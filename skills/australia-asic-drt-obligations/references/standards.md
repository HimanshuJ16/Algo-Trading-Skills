# Standards for ASIC Derivative Transaction Rules (Reporting) 2024

Regulatory instrument: **ASIC Derivative Transaction Rules (Reporting) 2024**
(F2022L01706), as amended.

| Standard | ISO Format | Requirement |
|---|---|---|
| **LEI** | ISO 17442 | 20 uppercase alphanumeric characters (18-char entity identifier + 2 numeric check digits). Checksum: ISO/IEC 7064 MOD 97-10 — the numeric representation must satisfy `value % 97 == 1`. |
| **UTI** | ISO 23897 | 20-52 uppercase alphanumeric characters, no separators. The first 20 characters are the generating entity's ISO 17442 LEI; the remaining up to 32 characters are a transaction-specific identifier. |
| **UPI** | ISO 4914 | 12 characters: a fixed "QZ" prefix followed by 9 base characters and 1 check character, each drawn from consonants (excluding A, E, I, O, U and Y) plus digits 0-9. |
| **Deadline** | Rule 2.2.3 | Report by the end of the second **business day** (T+2) after the transaction, or the fourth business day (T+4) where a value is required for Item 92 (linking identifier) of Table S1.1(1). Business days are measured in Sydney time. |

## Category
`regulatory-compliance-global`
