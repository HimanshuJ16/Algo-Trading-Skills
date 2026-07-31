# Standards for ICE Futures Integration

| Metric | Engineering Standard |
|---|---|
| FIX Tag 207 Routing | MIC MUST be explicitly set (`IFEU` for Europe, `IFUS` for US). |
| Contract Symbol Format | ICE symbols MUST be formatted as `<ROOT><MONTH><YY>` (e.g. `BZ26`). |
| NCR Reasonability Limit | Order price MUST NOT exceed contract NCR tick limit from current BBO. |
