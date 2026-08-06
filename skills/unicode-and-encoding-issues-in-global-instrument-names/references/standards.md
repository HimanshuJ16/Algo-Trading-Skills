# Institutional Unicode & Character Encoding Standards for Reference Data

## 1. Global Exchange Character Encoding Standards
| Region / Market | Standard Encoding | Byte Length | Common Corruption Issues | Priority Fallback Order |
| :--- | :--- | :--- | :--- | :--- |
| **Global Default** | `UTF-8` / `UTF-8-SIG` | 1–4 Bytes | Byte-Order-Mark (BOM `0xEF 0xBB 0xBF`) | 1 |
| **Japan (TSE/OSE)** | `Shift-JIS` / `CP932` | 1–2 Bytes | Misdecoded as `Latin-1` / `CP1252` | 2 |
| **China (SSE/SZSE)** | `GBK` / `GB2312` | 1–2 Bytes | Hanzi character corruption | 3 |
| **Korea (KRX)** | `EUC-KR` / `CP949` | 1–2 Bytes | Hangul character corruption | 4 |
| **Europe / Americas** | `ISO-8859-1` / `CP1252` | 1 Byte | Mojibake accent corruption (`SociÃ©tÃ©`) | 5 |

## 2. Unicode Normalization Forms (UAX #15)
- **NFC (Canonical Composition)**: **Mandatory Standard** for Security Master database primary keys and unique instrument constraints. Combines base characters + accents into single codepoints (`e` + `´` $\to$ `é` `U+00E9`).
- **NFD (Canonical Decomposition)**: Used during ASCII transliteration. Decomposes composed characters into base character + separate combining accent (`é` $\to$ `e` + `´`).
- **NFKC / NFKD (Compatibility Forms)**: Converts full-width Japanese katakana / Latin characters into standard half-width characters.

## 3. Invisible & Control Character Stripping Mandate
The reference data pipeline MUST strip the following non-printable codepoints:
- **Byte Order Mark (BOM)**: `U+FEFF`
- **Zero-Width Space**: `U+200B`
- **Zero-Width Non-Joiner**: `U+200C`
- **Zero-Width Joiner**: `U+200D`
- **Word Joiner**: `U+2060`
- **C0 / C1 Control Codes**: `U+0000` – `U+001F`, `U+007F` – `U+009F` (excluding `\t`, `\n`, `\r`)

