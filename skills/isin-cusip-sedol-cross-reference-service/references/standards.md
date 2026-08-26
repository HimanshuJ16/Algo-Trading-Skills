# Standards for Security Symbology Validation

All four schemes are described as "Modulo 10". They are **not** the same algorithm, and
substituting one for another accepts corrupted identifiers without raising anything.

| Identifier | Length | Issuing authority | Check-digit algorithm |
|---|---|---|---|
| ISIN | 12 | ISO 6166, via national numbering agencies (ANNA) | Luhn over the **expanded** digit string; total $\bmod\ 10 = 0$ |
| CUSIP | 9 | CUSIP Global Services (NNA for North America) | Double-add-double over characters 1–8 |
| SEDOL | 7 | London Stock Exchange | Weighted sum, weights $(1, 3, 1, 7, 3, 9)$ — nothing doubled |
| FIGI | 12 | ANSI X9.145-2021 / OMG; issued by Certified Providers | Double-add-double over characters 1–11 |

Character values are shared: digits are themselves, letters are $9 + $ alphabet position
(`A` = 10 … `Z` = 35, i.e. `ord(c) - 55`). SEDOL leaves the vowel positions in that
mapping *unused rather than closed*, which is why `J` = 19 even though `I` is never issued.

---

## ISIN — ISO 6166

**Format**: 2-character prefix + 9-character NSIN + 1 numeric check digit.

**Algorithm**: expand *every* character to its decimal value first — a letter becomes
**two** digits and shifts every doubling position after it — then apply Luhn right to
left over the expanded string. Expanding after applying Luhn, or treating a letter as a
single unit, yields a different and wrong result.

**Notes**
- For US and Canadian securities the NSIN is the CUSIP, so a US ISIN is `US` + CUSIP +
  a **newly computed** check digit: `037833100` → `US0378331005`.
- The prefix is the issuing NNA's code, not necessarily the issuer's domicile, and
  includes non-country allocations such as `XS` (Euroclear/Clearstream) and `EU`.
  Validating it against ISO 3166 rejects legitimate identifiers.
- Verified here against 15 real ISINs across 12 jurisdictions (US, GB, DE, AU, NL, FR,
  JP, CH, IE, CA, KY, XS), each confirmed live via the OpenFIGI mapping API (2026-08).

## CUSIP — ANSI X9.6-2020

**Format**: 6-character issuer number + 2-character issue number + 1 numeric check digit.
Administered by CUSIP Global Services, the National Numbering Agency for North America.

**Algorithm**: value each of characters 1–8, double the 1-indexed **even** positions,
sum the decimal digits of every resulting value, then
$\text{check} = (10 - \text{sum} \bmod 10) \bmod 10$.

**Character values**: `0-9` as themselves, `A-Z` as 10–35, and `*` = 36, `@` = 37,
`#` = 38 for Private Placement Numbers.

**Notes**
- A PPN CUSIP containing `*`, `@` or `#` **cannot** be embedded in an ISIN, whose NSIN
  field is alphanumeric only.
- `I` and `O` are confusable with `1` and `0` and are not known to have been issued, but
  ANSI X9.6 **does not forbid them**. This module therefore accepts them; rejecting them
  would enforce a convention the standard does not state. Contrast SEDOL, where the vowel
  exclusion *is* stated and *is* enforced.
- CINS (CUSIP International Numbering System) identifiers put a letter in the first
  position to denote country/region and are validated by the same algorithm.

Source: <https://blog.ansi.org/ansi/ansi-x9-62020-cusip/>

## SEDOL — London Stock Exchange

**Format**: a six-place alphanumeric code + 1 numeric check digit.

**Character set**: `0-9` plus the consonants `B C D F G H J K L M N P Q R S T V W X Y Z`.
**Vowels are never used** — enforce this, because `I`-for-`1` and `O`-for-`0` are exactly
the typos the rule exists to catch.

**Algorithm**: multiply characters 1–6 by $(1, 3, 1, 7, 3, 9)$ and sum. The check digit
carries weight 1, so the weighted total across all seven characters is a multiple of 10;
equivalently $\text{check} = (10 - \text{sum} \bmod 10) \bmod 10$. **No digit is doubled**
— this is not Luhn.

**Format history**: SEDOLs issued before 26 January 2004 are numeric-only; those issued
after are alphanumeric and begin with a letter, allocated sequentially from `B000009`.
This module validates the character set and the check digit but does **not** enforce the
numeric-vs-letter-prefixed structure, because the normative LSE specification is not
publicly available to confirm it as a hard rule rather than an issuance convention.

**Granularity**: LSEG describes SEDOL as the "identification of individual securities and
the markets that they are traded on" — one SEDOL per security **per market**. A
cross-listed issue therefore has one ISIN and several SEDOLs. SEDOL is also the UK NSIN
and is embedded in UK ISINs.

Sources: <https://en.wikipedia.org/wiki/SEDOL> ·
<https://www.lseg.com/en/data-analytics/market-data/data-analytics-pricing/data-symbology/sedol>

## FIGI — ANSI X9.145-2021 / OMG

**Syntax** (conformance clause 2.1):

| Position | Rule |
|---|---|
| 1–2 | Upper-case consonants including `Y`; **not** the sequences `BS`, `BM`, `GG`, `GB`, `VG` |
| 3 | The letter `G` |
| 4–11 | Consonants (including `Y`) or digits — no vowels |
| 12 | Numeric check digit |

The standard's normative pattern:

```
^(((?!BS|BM|GG|GB|VG)[BCDFGHJKLMNPQRSTVWXYZ]{2})G[BCDFGHJKLMNPQRSTVWXYZ\d]{8}\d)$
```

**Algorithm**: value characters 1–11, double the 1-indexed **even** positions, sum the
decimal digits, then $\text{check} = (10 - \text{sum} \bmod 10) \bmod 10$. X9.145 states
this is offset from ISIN's Luhn *deliberately*, "so as to result in a different check
digit than would be present in other similarly structured identifiers, e.g., ISIN" — the
check digit differs in over 90% of logically possible strings.

**Known discrepancy in the standard**: clause 6.1.2 (prose) lists the excluded prefixes as
`BS, BM, GG, GB, GH, KY, VG` (seven), while the conformance table and the normative regex
list five, omitting `GH` and `KY`. This module implements the **normative regex** and does
not exclude `GH`/`KY`. The consequence is real, not theoretical: `KYG875721634` — the
Cayman Islands ISIN of Tencent Holdings Ltd — satisfies the FIGI syntax rules and passes
the FIGI check digit, so it validates as both an ISIN and a FIGI. Ambiguity of this kind
is reported in `candidate_types`, never resolved silently.

**Prefix rationale**: the excluded sequences are those that could collide with a UK-family
ISIN, since the UK issues ISINs with `G` in the third position for its broader
jurisdiction (`BSG` Bahamas, `BMG` Bermuda, `GGG` Guernsey, `GBG` United Kingdom, `VGG`
British Virgin Islands).

**Worked example** carried in the standard: `NRG92C84SB39`. Values after doubling are
23, 54, 16, 18, 2, 24, 8, 8, 28, 22, 3, summing digit-wise to 71, so the check digit is
$80 - 71 = 9$.

Source: <https://x9.org/wp-content/uploads/2021/08/ANSI-X9.145-2021-Financial-Instrument-Global-Identifier-FIGI.pdf>

---

## What a check digit does not tell you

A check digit detects single-character substitutions and most transpositions in a string
someone typed or a feed truncated. It is not an existence proof. `BBG000MM82B1` passes the
FIGI check digit and is not Meta Platforms' FIGI (`BBG000MM2P62` is, per the OpenFIGI
mapping API). Only the issuing agency — CUSIP Global Services, the LSE SEDOL Masterfile,
the local NNA — or the OpenFIGI mapping API can confirm that an identifier was issued, is
still active, and points at the instrument you believe it does.
