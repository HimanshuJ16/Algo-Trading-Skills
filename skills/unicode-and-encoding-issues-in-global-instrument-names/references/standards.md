# Institutional Unicode & Character Encoding Standards for Reference Data

Everything below was verified 2026-09 against the cited source. Where a claim could not be
verified against a primary source it is marked as a convention, not a standard.

## 1. Encoding by region — and why the fallback order is a convention, not a detector

| Region / Market | Published encoding | Superset codec to try next | Common corruption | Python codec |
| :--- | :--- | :--- | :--- | :--- |
| **Global default** | `UTF-8` (with or without BOM) | — | BOM `0xEF 0xBB 0xBF` left in the string | `utf-8`, `utf-8-sig` |
| **Windows-authored vendor CSV** | `UTF-16LE` / `UTF-16BE` with BOM | — | BOM misread as CP1252 (`ÿþ...`) | `utf-16-le`, `utf-16-be` |
| **Japan (TSE/OSE)** | `Shift-JIS` (JIS X 0208) | `CP932` | NEC/IBM extension kanji rejected | `shift_jis`, `cp932` |
| **China (SSE/SZSE)** | `GBK` / `GB 2312` | `GB 18030` | Hanzi outside GBK rejected | `gbk`, `gb18030` |
| **Korea (KRX)** | `EUC-KR` | `CP949` (UHC) | Hangul outside the EUC-KR set rejected | `euc_kr`, `cp949` |
| **Europe / Americas** | `ISO-8859-1` / `CP1252` | — | Mojibake accent corruption | `cp1252`, `latin-1` |

**The order is not a detector.** These codecs overlap: bytes valid in one are frequently
valid in another and decode without error to different characters. Measured against
CPython 3.11's codecs, the CP932 encoding of `髙島屋` (Takashimaya, TSE 8233) is a valid GBK
sequence that decodes silently to `钹搰壆`. No fallback ordering resolves this, because both
decodes succeed. **Declare the venue's encoding.** Treat any result whose
`decode_confidence` is `guessed` as unverified.

**`cp932` is not a strict superset of `shift_jis`.** It accepts roughly 4,000 more two-byte
sequences, but six sequences map to different codepoints — 0x8160 is `U+301C` (WAVE DASH)
under `shift_jis` and `U+FF5E` (FULLWIDTH TILDE) under `cp932`, likewise 0x8161, 0x817C,
0x8191 and 0x8192. This is the well-known "wave dash" divergence between the JIS standard
mapping and the Microsoft vendor mapping. Order `cp932` *after* `shift_jis` so standard
bytes keep JIS mappings and only extension bytes reach `cp932`.

**GB 18030** is a mandatory PRC national standard and a superset of GBK and GB 2312. The
GB 18030-2022 revision became compulsory on 1 August 2023; it changes or removes 51
characters and adds over 10,000. CPython's `gb18030` codec implements the earlier revision,
so the 2022 deltas are **not** reflected — do not claim GB 18030-2022 conformance from
Python's stdlib alone. https://en.wikipedia.org/wiki/GB_18030

**Byte-order marks.** Check longest signature first: the UTF-32-LE BOM `FF FE 00 00` begins
with the UTF-16-LE BOM `FF FE`, so a two-byte test misidentifies every UTF-32-LE payload.

| Encoding | BOM |
| :--- | :--- |
| UTF-32-BE | `00 00 FE FF` |
| UTF-32-LE | `FF FE 00 00` |
| UTF-8 | `EF BB BF` |
| UTF-16-BE | `FE FF` |
| UTF-16-LE | `FF FE` |

A BOM-less UTF-16 payload of pure ASCII is **not** reliably detectable: it decodes as valid
UTF-8 containing NUL characters. Declaring the encoding is the only robust answer.

## 2. Unicode normalization forms (UAX #15)

Source: https://unicode.org/reports/tr15/

- **NFC (Canonical Composition)** — the form to key a security master on. Canonically
  equivalent strings share a binary representation only after normalization, so `é`
  (`U+00E9`) and `e`+`U+0301` compare equal only once both are NFC. This is a convention
  this repo adopts, not a regulatory mandate.
- **NFD (Canonical Decomposition)** — used inside transliteration to separate base letters
  from combining marks so the marks can be dropped.
- **NFKC / NFKD (Compatibility forms)** — additionally fold full-width Latin (`ＡＡＰＬ` →
  `AAPL`) and half-width katakana. Useful for matching, lossy for display. Compatibility
  folding is *not* canonical: it changes the text, so never store an NFKC form as the
  authoritative name.

**Normalization is not security.** NFC does not collapse homoglyphs; a Cyrillic `А`
(`U+0410`) stays distinct from Latin `A`. Confusable detection is UTS #39 work.

## 3. Invisible and control characters

Remove from names destined for a database index:

- **Byte order mark / zero-width no-break space**: `U+FEFF`
- **Zero-width space**: `U+200B`
- **Word joiner**: `U+2060`
- **C0 / C1 control codes**: `U+0000`–`U+001F`, `U+007F`–`U+009F` (excluding `\t`, `\n`, `\r`)
- **Surrogates and private-use codepoints** (Unicode categories `Cs`, `Co`)

**Handle the joining controls separately.** `U+200C` (ZWNJ) and `U+200D` (ZWJ) are *not*
noise in every script. UAX #31 §2.3: "The joining controls are used in the orthographies of
some languages, as well as in emoji ZWJ sequences."
(https://www.unicode.org/reports/tr31/) Removing `U+200C` from a Persian or Devanagari name
changes the spelling. Strip them by default for index hygiene, but make it configurable and
turn it off for feeds carrying those scripts.

**Order matters.** Strip control characters *after* Mojibake repair, not before: the
Latin-1 Mojibake of typographic punctuation *is* C1 control characters (`U+2019` becomes
`â` + `U+0080` + `U+0099`), and stripping first destroys the evidence the repair needs.

## 4. ASCII transliteration and FIX

**FIX carries the native name in an encoded field.** `EncodedSecurityDesc(351)` is the
"Encoded (non-ASCII characters) representation of the `SecurityDesc <107>` field in the
encoded format specified via the `MessageEncoding <347>` field", and "If used, the ASCII
(English) representation should also be specified in the `SecurityDesc <107>` field".
`MessageEncoding(347)` takes `ISO-2022-JP`, `EUC-JP`, `Shift_JIS` or `UTF-8`.

- https://www.onixs.biz/fix-dictionary/4.4/tagnum_351.html
- https://www.onixs.biz/fix-dictionary/4.4/tagnum_347.html

So the ASCII slug is the `SecurityDesc(107)` representation — a companion to the native
name, not a replacement for it.

**Transliteration must fold, not delete.** Canonical decomposition splits `Ü` into `U` plus
a combining mark, but it does not decompose these letters at all; without explicit
replacements an ASCII filter deletes them:

| Letter | Replacement used here | ICAO Doc 9303 Part 3 (MRZ) |
| :--- | :--- | :--- |
| `Æ` / `æ` | `AE` | `AE` |
| `Œ` / `œ` | `OE` | `OE` |
| `ß` / `ẞ` | `SS` | `SS` |
| `Þ` / `þ` | `TH` | `TH` |
| `Ð` / `ð`, `Đ` / `đ` | `D` | `D` |
| `Ł` / `ł` | `L` | `L` |
| `Ø` / `ø` | `O` | `OE` |

ICAO Doc 9303 Part 3 (machine-readable travel documents) is the reference table for the
first six rows: https://www.icao.int/publications/pages/publication.aspx?docnum=9303

**One deliberate departure.** ICAO transliterates `Ø` as `OE` and also expands `Ä`→`AE`,
`Ö`→`OE`, `Ü`→`UE`, `Å`→`AA`. This skill strips diacritics instead (`Ö`→`O`, `Ü`→`U`,
`Å`→`A`), so mapping `Ø`→`OE` while `Ö`→`O` would transliterate the same Nordic vowel two
different ways and split the security master key. `Ø`→`O` is chosen for internal
consistency. Override `ASCII_TRANSLITERATION_MAP` if you need MRZ-identical output.

**Transliteration is many-to-one and lossy.** `Müller` and `Muller` both slug to `MULLER`;
`トヨタ自動車` slugs to the empty string. The slug is a secondary lookup key and an ASCII
display form — never a primary key, and never a `Symbol(55)` value without checking whether
anything was dropped.

## 5. Auditability

Every conversion — declared vs guessed encoding, replacement characters substituted,
Mojibake repaired, characters stripped, characters dropped in transliteration — must be
recorded on the record, not just in a log line, so a later reviewer can tell a verified
name from a guessed one.
