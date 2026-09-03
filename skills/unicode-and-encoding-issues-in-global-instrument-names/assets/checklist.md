# Institutional Global Instrument Name Sanitization Checklist

## Decoding — establish provenance before content
- [ ] **Per-venue encoding declared**: every feed has a `source_encoding` in configuration
      (e.g. `XTKS` → `cp932`, `XKRX` → `cp949`, `XSHG` → `gb18030`). Guessing is the
      fallback, not the design.
- [ ] **Declared-encoding mismatch fails the record**: a declared codec that does not decode
      raises rather than falling through to a guess. A plausible wrong name is worse than a
      failed record.
- [ ] **BOM detection covers UTF-8, UTF-16 and UTF-32**, testing 4-byte signatures before
      2-byte ones (the UTF-32-LE BOM `FF FE 00 00` starts with the UTF-16-LE BOM `FF FE`).
- [ ] **Superset CJK codecs are tried after, not instead of, the narrow ones**: `cp932` after
      `shift_jis`, `gb18030` after `gbk`, `cp949` after `euc_kr`. `cp932` remaps six
      sequences (the "wave dash" divergence), so it must not pre-empt `shift_jis`.
- [ ] **`latin-1` is last**: it maps all 256 byte values and never raises, so anything
      after it is unreachable.
- [ ] **Decode confidence is recorded on the record**, not only logged, and `guessed` /
      `lossy` results are routed to review instead of written unattended.
- [ ] **Replacement characters are counted**: any `U+FFFD` in a persisted name is a defect,
      not a cleaned value.

## Repair, stripping and normalization — order is load-bearing
- [ ] **Mojibake repair runs BEFORE control stripping**: the Latin-1 Mojibake of `U+2019` is
      `â` + `U+0080` + `U+0099`, and two of those are C1 controls that stripping destroys.
- [ ] **Repair is a strict byte round trip, not a substitution table**: CP1252 first, then
      Latin-1, accepted only if the strict UTF-8 decode succeeds and the corruption score
      strictly falls. Bounded round count (doubly-encoded Mojibake exists).
- [ ] **No single-character key in any residual substitution table**: a bare `"Ã"` entry
      corrupts correctly encoded uppercase Portuguese (`SÃO MARTINHO S.A.`, B3: SMTO3).
- [ ] **Correctly encoded input is verified byte-identical on output** for a fixture set of
      real names carrying Latin-1-range letters (`Ângelo`, `AÇÃO`, `NÉSTLÉ`, `ÅF Pöyry`,
      `Ørsted`).
- [ ] **Sanitization is idempotent**: sanitizing an already-sanitized name changes nothing.
- [ ] **Invisibles removed**: `U+FEFF`, `U+200B`, `U+2060`, and Unicode `C*` categories
      except `\t`, `\n`, `\r`.
- [ ] **Joining controls handled per script**: `U+200C`/`U+200D` stripping is configurable
      and turned OFF for Persian, Arabic and Indic feeds (UAX #31 §2.3 — the joining
      controls are used in the orthographies of some languages).
- [ ] **NFC enforced for security master keys**; NFKC used only where full-width/half-width
      folding is intended for matching, never stored as the authoritative name.
- [ ] **A name emptied by sanitization raises**: zero-width-only or control-only input must
      not produce an empty `cleaned_name`.

## ASCII transliteration and FIX emission
- [ ] **Non-decomposable Latin letters are folded, not deleted**: `Ø`, `ß`, `Ł`, `Æ`, `Œ`,
      `Þ`, `Ð`, `Đ` have explicit replacements. Verify `Ørsted A/S` → `ORSTED A/S`, not
      `RSTED A/S`, and `Straße` → `STRASSE`, not `STRAE`.
- [ ] **Untransliterable characters are reported**: `ascii_slug_is_lossy` /
      `dropped_characters` are checked before use.
- [ ] **An empty or lossy slug is never written to `Symbol(55)`**: `トヨタ自動車` slugs to
      `""`, which is an unroutable order rather than a data-quality warning.
- [ ] **FIX split is correct**: native name in `EncodedSecurityDesc(351)` with
      `MessageEncoding(347)`; the ASCII representation in `SecurityDesc(107)`.
- [ ] **The slug is a secondary key only**: transliteration is many-to-one (`Müller` and
      `Muller` both slug to `MULLER`), so a slug match is a match *candidate* requiring
      confirmation, never a unique constraint.

## Security master database hygiene
- [ ] **Columns store UTF-8** (`NVARCHAR` / `TEXT`) with a collation that does not silently
      fold case or accents behind the application's back.
- [ ] **Uniqueness is on the NFC name**, not the slug.
- [ ] **Audit trail persisted with the record**: encoding + confidence, Mojibake repairs,
      characters stripped, characters dropped in transliteration, and all warnings.
- [ ] **Homoglyph risk acknowledged**: NFC does not collapse a Cyrillic `А` into a Latin
      `A`. If confusable detection is required, that is separate UTS #39 work.
