---
name: isin-cusip-sedol-cross-reference-service
description: >-
  Security master cross-reference service validating ISIN (ISO 6166 Luhn), CUSIP (ANSI X9.6 double-add-double), SEDOL (LSE weighted sum) and FIGI (ANSI X9.145) check digits, and resolving any of them to one canonical record keyed on an immutable OpenFIGI identifier.
domain: Data Management Global
subdomain: Security Master & Symbology Resolution
tags: ["isin", "cusip", "sedol", "figi", "security-master", "checksum-validation", "symbology-resolution"]
brokers_frameworks: ["OpenFIGI API", "CUSIP Global Services", "LSE SEDOL Masterfile", "ANSI X9.145 FIGI", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when building a Security Master, ingesting multi-vendor reference data (Bloomberg, Refinitiv, FactSet), or validating symbols at order entry. Global securities carry disparate identifiers — **ISIN** (12 characters, global, ISO 6166), **CUSIP** (9 characters, North America, ANSI X9.6), **SEDOL** (7 characters, LSE), and **FIGI** (12 characters, ANSI X9.145) — and a typo in any of them silently corrupts every downstream join keyed on it.

The four are all "Modulo 10" schemes, and that description is the trap: **the algorithms are different and are not interchangeable.**

| Identifier | Algorithm | Detail |
|---|---|---|
| ISIN | Luhn over the *expanded* digit string | Each character becomes its value first (A=10 … Z=35, so a letter yields **two** digits), then Luhn right-to-left; total $\bmod 10 = 0$ |
| CUSIP | Double-add-double over characters 1–8 | 1-indexed **even** positions doubled; check digit $= (10 - \text{sum} \bmod 10) \bmod 10$ |
| SEDOL | Weighted sum, weights $(1, 3, 1, 7, 3, 9)$ | **Nothing is doubled.** Not Luhn |
| FIGI | Double-add-double over characters 1–11 | 1-indexed **even** positions doubled — deliberately offset from ISIN by the standard |

## When NOT to Use

- **As proof a security exists.** A check digit catches single-character substitutions and most transpositions. It says nothing about whether the identifier was ever issued, is still active, or points at the instrument you think it does. Only the issuing agency (CUSIP Global Services, the LSE SEDOL Masterfile, the local National Numbering Agency) or the OpenFIGI mapping API can answer that. This module makes no network calls.
- **As a multi-listing security master.** An ISIN identifies an *issue*; a SEDOL identifies one security **on one market**. A cross-listed issue therefore has one ISIN and several SEDOLs. The flat one-record-per-security schema here models a single listing — a real multi-venue master needs a one-to-many SEDOL (and FIGI) table. See `reference-data-symbol-mapping-across-vendors`.
- **To infer an identifier's type when you already know it.** Length-and-shape inference cannot separate a 7-character ticker from a SEDOL, or a Cayman ISIN from a FIGI. A vendor feed column is labelled; pass `identifier_type=` and skip the guessing entirely.
- **As a substitute for corporate-action handling.** A ticker rename or share-class reorganisation changes the mapping, not the check digit. See `corporate-action-event-calendar-integration` and `instrument-universe-change-detection-and-alerting`.
- **As a CUSIP redistribution vehicle.** CUSIP data is licensed from CUSIP Global Services. Validating a check digit is arithmetic on a string you already hold; publishing a CUSIP database is a licensing question, not a technical one.

## Prerequisites

- An identifier string (ISIN, CUSIP, SEDOL, FIGI or ticker), and — strongly preferred — the type the source feed says it is.
- Security master rows as `SecurityMasterRecord` (`isin`, `cusip`, `sedol`, `figi`, `ticker_symbol`, `asset_name`, `country_code`). Any of the four structured identifiers may be an empty string where the issue has none.
- A decision on `strict_validation`: raise on bad master data (default), or load it and audit via `validate_master_data()`.

## Workflow

1. **Validate the master data before it can corrupt a join**:
   - On construction the engine re-validates every identifier on every row against its own algorithm and indexes each type separately. A row whose SEDOL fails its own check digit is a data defect, not a lookup problem — it is reported by `validate_master_data()` and raises under `strict_validation=True`.
   - Offending identifiers are still indexed under `strict_validation=False`. Refusing to index them would make the row unreachable by a key live systems may already be joining on, which trades a visible defect for an invisible one.
   - Duplicate identifiers across rows are reported, and the first row wins. Two rows claiming one ISIN is an upstream merge error; resolving it silently hides it.
2. **Normalise, then classify by syntax — never by length alone**:
   - Uppercase, strip surrounding whitespace, and strip embedded spaces and hyphens *for structured matching only* (vendors ship CUSIPs as `037833-10-0`). Ticker matching uses the string as typed, because `-` and `.` are meaningful in ticker symbols (`BRK.B`).
   - Test the normalised string against each identifier's full syntax rule, then its check digit. A string can satisfy more than one: `KYG875721634` (Tencent Holdings) is a valid ISIN **and** satisfies the FIGI syntax rules **and** passes the FIGI check digit. Report every type that validated in `candidate_types` rather than silently choosing.
   - When more than one type validates, prefer whichever one the master data actually resolves — that is evidence. Fall back to the documented `CLASSIFICATION_PRIORITY` only when the data cannot break the tie.
3. **Separate "corrupted" from "unknown"**:
   - A string shaped like a structured identifier whose check digit fails is `INVALID_CHECKSUM` — reject it at the boundary, do not attempt a lookup.
   - A string matching no structured syntax falls through to `TICKER` and is looked up without a check digit. The report sets `checksum_applied=False` so `is_checksum_valid=True` cannot be misread as "verified".
4. **Resolve against a type-scoped index**:
   - Look the query up in the index for its resolved type only. Searching the union of all five fields lets a query validated as a CUSIP resolve on some unrelated row's *ticker*.
5. **Emit `IdentifierCrossReferenceReport`** carrying `query_type`, `candidate_types`, `is_checksum_valid`, `checksum_applied`, `status` and `audit_notes` — enough for a reviewer to see not just the answer but how ambiguous the question was.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Running one algorithm against another identifier.** SEDOL is a plain weighted sum; applying Luhn to it accepts corrupted SEDOLs. FIGI's doubling is offset from ISIN's *by design* — ANSI X9.145 says so explicitly, so that the same string generally gets a different check digit under the two schemes. Reusing the ISIN routine for FIGI is a silent correctness failure.
- **Admitting vowels into a SEDOL.** Vowels are never used, so the character set is `0-9` plus the consonants `BCDFGHJKLMNPQRSTVWXYZ`. A permissive `[B-Z0-9]` class admits `E`, `I`, `O` and `U` — and `I`-for-`1` and `O`-for-`0` are the exact typos the excluded-vowel rule exists to catch.
- **Assuming 12 characters means ISIN.** A FIGI is also 12 characters, also starts with two letters, and also ends in a digit. Classifying on length alone routes every FIGI into the ISIN validator, where it fails the check digit and gets rejected as corrupt.
- **Trusting a check digit as an existence proof.** `BBG000MM82B1` passes the FIGI check digit perfectly and is not Meta's FIGI. Arithmetic validates typing; only the issuing agency validates existence.
- **Assuming a US ISIN equals its CUSIP.** A US ISIN is `US` + the 9-character CUSIP + a **newly computed** check digit (`037833100` → `US0378331005`). The CUSIP's own check digit is not reusable. Note also that a Private Placement Number CUSIP containing `*`, `@` or `#` cannot be embedded in an ISIN at all.
- **Calling `int()` on a check digit position the regex never constrained.** A CUSIP pattern of `[A-Z0-9]{9}` admits a trailing letter, and the subsequent `int()` raises instead of returning False — one malformed vendor row kills the whole ingest loop. Constrain the check-digit position in the pattern.
- **Keying the master on ticker rather than FIGI.** Tickers are reassigned and renamed; `FB` became `META` while the FIGI `BBG000MM2P62` did not move. Join on the FIGI and carry the ticker as an attribute.
- **Treating one SEDOL as the security.** A cross-listed issue has one ISIN and a SEDOL per market. A `UNIQUE` constraint on a single SEDOL column silently drops listings.

## Verification

- Instantiate `IsinCusipSedolCrossReferenceEngine()`. Confirm `validate_master_data()` returns `()` — every shipped row's ISIN, CUSIP, SEDOL and FIGI must survive its own validator, and each must resolve back to its own row.
- Look Apple Inc up by all five keys — ISIN `US0378331005`, CUSIP `037833100`, SEDOL `2046251`, FIGI `BBG000B9XRY4`, ticker `AAPL` $\implies$ each returns `MATCH_FOUND` with the matching `query_type`. Look up `037833-10-0` $\implies$ the hyphenated CUSIP normalises and still resolves.
- Audit a corrupted ISIN (`US0378331009`) $\implies$ `status == "INVALID_CHECKSUM"`, `query_type == "ISIN"`, `is_checksum_valid` false, no record returned.
- Verify the ambiguity path: look up `KYG875721634` $\implies$ `candidate_types == ("ISIN", "FIGI")` and `audit_notes` flags `AMBIGUOUS`.
- Verify the FIGI implementation against the worked example carried in ANSI X9.145-2021 itself: `NRG92C84SB39` $\implies$ valid, and every other check digit for that 11-character stem $\implies$ invalid.
- Verify master-data enforcement: construct a record carrying SEDOL `BNPYS71` $\implies$ `ValueError` under `strict_validation=True`, and a single reported problem naming the SEDOL under `strict_validation=False`.
- Run `python -m unittest discover -s skills/isin-cusip-sedol-cross-reference-service/scripts`.

## Related Skills

- `reference-data-symbol-mapping-across-vendors`
- `reference-data-golden-source-designation`
- `instrument-universe-change-detection-and-alerting`
- `corporate-action-event-calendar-integration`
- `unicode-and-encoding-issues-in-global-instrument-names`
