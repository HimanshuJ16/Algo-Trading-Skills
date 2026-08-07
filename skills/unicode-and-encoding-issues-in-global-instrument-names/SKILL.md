---
name: unicode-and-encoding-issues-in-global-instrument-names
description: "Institutional reference data skill for resolving Unicode character encoding issues, byte-order-mark (BOM) stripping, Mojibake text repair, NFC/NFD normalization, invisible control character removal, and ASCII slug generation across global exchange feeds."
domain: Global Reference Data & Security Master
subdomain: Character Encoding & Data Sanitization
tags:
- unicode
- encoding
- mojibake
- nfc-normalization
- reference-data
- security-master
- ascii-slug
- multi-exchange
brokers_frameworks:
- quickfix
- pandas
- sqlalchemy
version: "1.1.0"
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when processing multi-exchange reference data feeds, corporate action announcements, and security master files containing international security names across varied character encodings (Japanese Shift-JIS, Chinese GBK, Korean EUC-KR, European Latin-1 / Windows-1252, and UTF-8).

This skill provides institutional mechanisms to:
- Strip UTF-8 Byte-Order-Marks (`U+FEFF`), zero-width spaces (`U+200B`), and non-printable control characters.
- Detect and decode raw byte streams using fallback rules prioritizing multibyte CJK encodings over single-byte fallbacks.
- Automatically detect and repair **Mojibake** text corruptions (e.g. `SociÃ©tÃ©` -> `Société`).
- Apply **Unicode NFC Normalization** to prevent duplicate security master keys caused by composed vs decomposed Unicode variants.
- Generate clean **ASCII Transliterated Slugs** for legacy FIX Tag 55 and exchange interfaces.

## Prerequisites

- Python 3.9+
- Standard Python libraries (`unicodedata`, `re`).
- Security Master database supporting UTF-8 string columns (`NVARCHAR` or `TEXT`).

## Workflow

1. **Configure Sanitizer Settings**: Instantiate `InstrumentSanitizerConfig` specifying target normalization (`NFC`), default encoding (`utf-8`), fallback encodings (`shift_jis`, `gbk`, `euc-kr`, `latin-1`), and ASCII slug generation preferences.
2. **Decode Raw Byte Feeds**: Pass raw byte streams or string names to `decode_bytes()` to remove UTF-8 BOM markers and resolve multibyte encodings.
3. **Repair Mojibake Corruptions**: Call `repair_mojibake()` to detect and fix UTF-8 -> Latin-1 text corruptions.
4. **Strip Control & Invisible Characters**: Invoke `strip_control_and_zero_width_chars()` to purge non-printable C0/C1 characters and zero-width spaces.
5. **Normalize & Transliterate**: Execute `sanitize_instrument_name()` to produce a standardized NFC Unicode name (`cleaned_name`) and an uppercase transliterated ASCII slug (`ascii_slug`).

## Common Pitfalls

- **Placing `latin-1` First in Fallback Lists**: Because `latin-1` maps all bytes (0x00-0xFF) without raising `UnicodeDecodeError`, placing `latin-1` before multibyte encodings (`shift_jis`, `gbk`) prevents CJK encodings from ever being evaluated.
- **Failing to Normalize Unicode (NFC vs NFD)**: The same string `é` can be encoded as a single codepoint `U+00E9` (NFC) or decomposed `e` + `U+0301` (NFD). Comparing un-normalized strings causes duplicate instrument creation in Security Master databases.
- **Ignoring Invisible Zero-Width Characters**: Copy-pasting company names from PDF/HTML feeds often introduces hidden zero-width space characters (`U+200B`) that break database index lookups.
- **Stripping Accents Instead of Transliterating**: Simple ASCII stripping converts `Münchener` to `Mnchener`. Always use `NFD` decomposition before stripping combining characters to output clean `MUNCHENER`.

## Verification

Run the unit test suite to validate byte decoding, BOM stripping, Shift-JIS fallback, Mojibake repair, NFC normalization, and ASCII slug transliteration:

```bash
python -m unittest discover -s skills/unicode-and-encoding-issues-in-global-instrument-names/scripts
```

## Related Skills

- `vendor-specific-adjustment-methodology-reconciliation`
- `binary-protocol-parsing-for-low-latency-feeds`
- `zero-downtime-database-schema-migrations`
- `corporate-action-adjusted-backtesting`

