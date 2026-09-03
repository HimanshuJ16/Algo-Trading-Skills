---
name: unicode-and-encoding-issues-in-global-instrument-names
description: "Institutional reference data skill for resolving Unicode character encoding issues in global instrument names: byte-order-mark (BOM) detection across UTF-8/16/32, round-trip Mojibake repair, NFC/NFD normalization for security master keys, invisible and control character removal, and lossy-aware ASCII transliteration for FIX SecurityDesc and legacy exchange interfaces."
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
- sqlalchemy
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when ingesting instrument names from multi-exchange reference data feeds,
corporate action announcements, or security master files whose text arrives in mixed
encodings (Japanese Shift-JIS/CP932, Chinese GBK/GB 18030, Korean EUC-KR/CP949, European
Latin-1/CP1252, UTF-8/16/32), and a wrong name silently becomes a duplicate or mismatched
security master record.

It provides mechanisms to:
- Decode raw feed bytes with a **declared** venue encoding, or detect a UTF-8/UTF-16/UTF-32
  byte-order mark, falling back to a guess that is explicitly labelled as a guess.
- Repair **Mojibake** by strict byte round trip (`SociÃ©tÃ© Générale` → `Société Générale`,
  `Lâ€™OrÃ©al` → `L’Oréal`) without rewriting correctly encoded text.
- Strip BOMs (`U+FEFF`), zero-width spaces (`U+200B`), word joiners (`U+2060`) and
  non-printable control characters, with joining controls (`U+200C`/`U+200D`) optional.
- Apply **NFC normalization** (UAX #15) so composed and decomposed spellings of the same
  name resolve to one security master key.
- Produce an **ASCII transliteration** for FIX `SecurityDesc(107)`/`Symbol(55)` that folds
  letters instead of deleting them, and reports what it could not fold.

## When NOT to Use

- **Symbol cross-referencing** — mapping a vendor ticker to a canonical instrument is
  `reference-data-symbol-mapping-across-vendors`. This skill sanitizes names, not
  identifiers.
- **Identifier validation** — ISIN/CUSIP/SEDOL check digits are
  `isin-cusip-sedol-cross-reference-service`.
- **Homoglyph or confusable detection** — a ticker written with a Cyrillic `А` (`U+0410`)
  normalizes and slugs cleanly here. NFC does not collapse confusables; that is UTS #39
  work and is out of scope.
- **As a primary key** — the ASCII slug is many-to-one (`Müller` and `Muller` both slug to
  `MULLER`). Key on `cleaned_name` (NFC); treat a slug match as a candidate to confirm.
- **When the feed is already UTF-8 and verified end to end** — running a Mojibake repairer
  over clean data buys nothing and adds a small false-positive surface.

## Prerequisites

- Python 3.9+. Standard library only (`unicodedata`, `re`, `logging`, `dataclasses`) — no
  third-party dependency.
- A per-venue encoding declaration (which codec each feed actually publishes). Without it
  the engine can only guess, and it will say so.
- Security master columns that store UTF-8 (`NVARCHAR`/`TEXT`), with the ASCII slug held
  as a **secondary**, non-unique lookup column.

## Workflow

1. **Declare the venue encoding.** Set `InstrumentSanitizerConfig.source_encoding` (or pass
   `source_encoding=` per call) to the codec the venue actually publishes. A declared
   encoding is applied strictly and a mismatch raises `UnicodeProcessingError` — it does
   **not** fall through to a guess, because a plausible wrong name is worse than a failed
   record. Only omit it when the feed genuinely mixes encodings.
2. **Decode.** `decode_payload()` returns a `DecodedPayload` carrying `confidence`
   (`declared` / `bom` / `default` / `guessed` / `lossy`). Branch on it: `guessed` and
   `lossy` results must be queued for review, never written unattended. `decode_bytes()`
   remains available but discards the confidence signal — prefer `decode_payload()`.
3. **Repair Mojibake before touching control characters.** `repair_mojibake()` re-encodes
   to CP1252, then Latin-1, and re-decodes as *strict* UTF-8, repeating up to three times
   for doubly-encoded text. The strict decode is what makes it safe on clean input. Do not
   reorder this after step 4.
4. **Strip invisibles.** `strip_control_and_zero_width_chars()`. For Persian, Arabic and
   Indic names pass `strip_joiner_controls=False` (or set it on the config): `U+200C`
   carries meaning in those orthographies, so removing it changes the spelling.
5. **Normalize.** NFC for security master keys. Use NFKC only when you also want full-width
   Latin and half-width katakana folded — good for matching, wrong for display.
6. **Transliterate, then check the loss.** `sanitize_instrument_name()` returns
   `cleaned_name` (the NFC key) and `ascii_slug`. **If `ascii_slug_is_lossy` is true, the
   slug does not represent the name** — a CJK or Hebrew name yields `""`. Route the native
   name to FIX `EncodedSecurityDesc(351)` with `MessageEncoding(347)`, and only write
   `SecurityDesc(107)`/`Symbol(55)` from a non-lossy slug or a human-supplied romanization.
7. **Gate the write.** `result.is_trustworthy` is false whenever anything was guessed,
   lossy, or untransliterable. Persist `audit_actions` and `warnings` alongside the record.

## Common Pitfalls

- **Repairing Mojibake with a substitution table.** A table cannot distinguish corruption
  from correct text. `SÃO MARTINHO S.A.` (B3: SMTO3) is valid, correctly encoded
  Portuguese; a table containing a bare `"Ã"` key silently rewrites it to `SÁO MARTINHO
  S.A.`. Repair by strict round trip and let the UTF-8 decode failure protect clean input.
- **Stripping control characters before repairing Mojibake.** The Latin-1 Mojibake of
  `L’Oréal` is `Lâ\x80\x99Oréal` — two of those bytes are C1 control characters. Strip
  first and the repair has nothing left to work with; you get `LâOréal` and a
  `contains_mojibake` flag that lies about having fixed it.
- **Only handling the Latin-1 form of Mojibake.** The common real-world form is CP1252:
  `U+2019` becomes `â€™`, and `€`/`™` cannot be encoded back to Latin-1 at all. Try CP1252
  first.
- **Assuming `errors="ignore"` transliterates.** NFD decomposes `Ü` but does **not**
  decompose `Ø`, `ß`, `Ł`, `Æ` or `Đ`, so "decompose, drop combining marks, encode ASCII
  ignoring errors" turns `Ørsted A/S` (CPH: ORSTED) into `RSTED A/S` and `Straße` into
  `STRAE`. Non-decomposable letters need explicit replacements.
- **Treating an empty ASCII slug as a valid symbol.** `トヨタ自動車` transliterates to `""`.
  Writing that into `Symbol(55)` produces an unroutable order, not a data-quality warning.
  Check `ascii_slug_is_lossy`.
- **Trusting fallback encoding detection.** Legacy CJK codecs decode each other's bytes
  without error: the CP932 bytes for `髙島屋` (TSE 8233) are a *valid* GBK sequence that
  decodes to `钹搰壆`. Reordering the fallback list cannot fix this — only a declared
  encoding can.
- **Putting `latin-1` first in a fallback list.** `latin-1` maps all 256 byte values and
  never raises, so anything after it is dead code.
- **Comparing un-normalized names.** `é` as `U+00E9` and as `e`+`U+0301` are different
  strings; both spellings of one issuer create two security master rows.
- **Stripping `U+200C`/`U+200D` globally.** UAX #31 §2.3 notes the joining controls are
  used in the orthographies of some languages; removing them from a Persian or Devanagari
  name changes the word rather than cleaning it.

## Verification

```bash
python -m unittest discover -s skills/unicode-and-encoding-issues-in-global-instrument-names/scripts
```

The suite asserts against real listed-company spellings (Ørsted A/S, São Martinho S.A.,
髙島屋, L’Oréal, Société Générale) and includes regression tests for every defect listed
under Common Pitfalls. Confirm in particular that correctly encoded input is returned
byte-identical (`test_correctly_encoded_names_pass_through_unchanged`) and that
sanitization is idempotent.

## Related Skills

- `reference-data-symbol-mapping-across-vendors`
- `vendor-specific-adjustment-methodology-reconciliation`
- `binary-protocol-parsing-for-low-latency-feeds`
- `zero-downtime-database-schema-migrations`
- `corporate-action-adjusted-backtesting`
