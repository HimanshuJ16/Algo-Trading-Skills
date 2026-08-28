# Pre-Flight Checklist — Nasdaq TotalView-ITCH 5.0 Parsing

## Wire format
- [ ] Is Big-Endian `>` used for every struct layout (never `<` or `=`)?
- [ ] Does each layout's size plus the type byte equal the spec total — `A` 36, `F` 40, `E` 31, `C` 36, `X` 23, `D` 19, `U` 35, `P` 44?
- [ ] Is the raw integer price divided by 10,000, and is the integer tick kept as the authoritative value?
- [ ] Is a price above `0x77359400` (200,000.0000) treated as a scaling error rather than a real quote?
- [ ] Is the 6-byte timestamp decoded as 48-bit big-endian, not 32- or 64-bit?
- [ ] Are Alpha fields right-stripped only, so a leading space still signals a bad offset?

## Framing
- [ ] Is the MoldUDP64 20-byte header and each block's 2-byte length prefix stripped before the decoder sees the message?
- [ ] Are unknown message types skipped by their transport-declared length, never by a guessed one?
- [ ] Is sequence-gap detection running upstream of the decoder?

## Message coverage
- [ ] Are **both** Add variants handled — `A` and `F` (with MPID Attribution)?
- [ ] Are **both** execution types handled — `E` and `C` (Executed With Price)?
- [ ] Is `X` treated as a partial reduction and `D` as a full removal?
- [ ] Is `U` Replace handled: original removed, replacement inserted under the **new** reference number, side and stock inherited from the original, Shares taken as an absolute quantity?
- [ ] Is `P` Trade routed **away** from the book, with its zero reference number and constant `B` side ignored?

## Book integrity
- [ ] Is an `E`/`C`/`X`/`D`/`U` for an unknown order counted as `UNKNOWN_ORDER` rather than silently absorbed?
- [ ] Is deducting more shares than are resting flagged as `OVER_EXECUTE` / `OVER_CANCEL` instead of clamped to zero?
- [ ] Is a reused day-unique reference number flagged as `DUPLICATE_ORDER_ID`?
- [ ] Is a backwards timestamp flagged, while equal timestamps are accepted?
- [ ] Is a `U` whose original is missing left uncreated, rather than synthesised on a guessed side?

## Sign-off
- [ ] Was `strict` set deliberately for this run (raise in production, count in research)?
- [ ] Is `integrity_violation_count == 0` before any microstructure statistic from this replay is reported?
- [ ] Is the reconstructed book reconciled against a snapshot (GLIMPSE) or an independent archive?
