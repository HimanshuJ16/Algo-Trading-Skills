# Institutional Global Instrument Name Sanitization Checklist

## Ingestion & Encoding Fallback Setup
- [ ] **Byte-Order-Mark (BOM) Stripping**: Configure decoder to automatically strip UTF-8 BOM (`b"\xef\xbb\xbf"` / `U+FEFF`).
- [ ] **Fallback Encoding Priority**: Verify fallback encoding order puts multibyte CJK encodings (`shift_jis`, `gbk`, `euc-kr`) BEFORE single-byte fallbacks (`latin-1`, `cp1252`).
- [ ] **Invisible Control Character Purging**: Verify removal of zero-width spaces (`U+200B`, `U+200C`, `U+200D`, `U+2060`) and non-printable control codes.

## Mojibake Repair & Normalization
- [ ] **Mojibake Detection**: Enable `repair_mojibake()` to automatically fix UTF-8 bytes misdecoded as Latin-1 (e.g. `SociÃ©tÃ©` $\to$ `Société`).
- [ ] **NFC Unicode Normalization**: Enforce `NFC` (Canonical Composition) as the standard normalization form for all Security Master primary keys.
- [ ] **ASCII Transliterated Slug Generation**: Generate uppercase ASCII slugs using NFD decomposition for legacy FIX Tag 55 and exchange interfaces.

## Security Master Database Hygiene
- [ ] **Database Column Encoding**: Verify Security Master database tables use `UTF-8` or `NVARCHAR` collation.
- [ ] **Duplicate Instrument Prevention**: Audit Security Master index lookups using both NFC normalized names and ASCII slugs.
- [ ] **Audit Trail Archival**: Log all encoding conversions, Mojibake repairs, and character stripping actions in data pipeline logs.

