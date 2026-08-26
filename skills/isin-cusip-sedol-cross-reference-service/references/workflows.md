# Workflows for Symbology Cross-Referencing

## 1. Load and self-validate the security master

1. Build `SecurityMasterRecord` rows. Any of `isin`, `cusip`, `sedol`, `figi` may be an
   empty string where the issue has no such identifier — a UK-only line has no CUSIP, an
   unlisted line has no SEDOL. Empty is absence, not a defect.
2. Construct `IsinCusipSedolCrossReferenceEngine(records=...)`. On construction the engine
   re-validates every non-empty identifier against **its own** algorithm and builds one
   index per identifier type.
3. Handle the outcome:
   - `strict_validation=True` (default) raises `ValueError` listing every problem. Use
     this for a nightly master rebuild, where a bad row should stop the pipeline.
   - `strict_validation=False` logs and records the problems; read them from
     `validate_master_data()`. Offending identifiers are still indexed, so joins that
     already exist keep resolving while the defect is visible.
4. Duplicate identifiers across rows are reported and the first row wins. Two rows
   claiming one ISIN is an upstream merge error — surface it, do not silently pick.

## 2. Normalise the query

1. Uppercase and strip surrounding whitespace.
2. Strip embedded spaces and hyphens **for structured matching only** — vendors deliver
   CUSIPs hyphenated 6-2-1 (`037833-10-0`).
3. Keep the string as typed for ticker matching: `-` and `.` are meaningful in ticker
   symbols (`BRK.B`, `BA.`).

## 3. Classify by syntax and check digit — not by length

1. If the caller supplied `identifier_type`, use it. **Prefer this path.** A feed column
   is labelled, and inference cannot separate a 7-character ticker from a SEDOL.
2. Otherwise test the normalised string against each type's full syntax rule, then its
   check digit. `classify_identifier` returns two tuples:
   - `validated` — syntax *and* check digit both pass.
   - `syntactic` — syntax passes, check digit does not. That string is shaped like the
     identifier and is corrupted.
3. More than one entry in `validated` means genuine ambiguity — see `references/standards.md`
   on the ISIN/FIGI overlap. Report all of them in `candidate_types`.

## 4. Resolve

1. When `validated` is non-empty, prefer whichever candidate the master data actually
   resolves; that is evidence, where `CLASSIFICATION_PRIORITY` is only a convention. Fall
   back to the priority order when the data cannot break the tie.
2. When `syntactic` is non-empty but `validated` is empty, return `INVALID_CHECKSUM`.
   Reject at the boundary — do not attempt a lookup with a corrupted key.
3. When neither is populated, fall through to `TICKER`, look up without a check digit, and
   set `checksum_applied=False`.
4. Search the index for the resolved type **only**. Scanning the union of all five fields
   lets a query validated as a CUSIP resolve on some unrelated row's ticker.

## 5. Emit the audit report

`IdentifierCrossReferenceReport` carries `query_identifier`, `query_type`,
`candidate_types`, `is_checksum_valid`, `checksum_applied`, `matched_record`, `status`
(`MATCH_FOUND` / `INVALID_CHECKSUM` / `IDENTIFIER_NOT_FOUND`) and `audit_notes`. Persist
it: a reviewer needs to see not only which record was returned but how ambiguous the
question was and whether a check digit was applied at all.

## 6. Escalate beyond arithmetic

A passing check digit is not an existence proof. Before an identifier reaches production
routing, confirm it against the issuing agency or the OpenFIGI mapping API, and key
persistent joins on the FIGI rather than the ticker — tickers get reassigned, FIGIs do not.
