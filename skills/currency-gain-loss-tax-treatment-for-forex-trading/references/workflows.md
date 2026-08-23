# Workflows for Forex Currency Gain/Loss Tax Treatment

**US federal only. Not tax advice.** Citations in `references/standards.md`.

1. **Scope the tax year**:
   - Pass `tax_year`; records are filtered on the year in `trade_date`.
   - Omitting it includes every record supplied and mixes tax years silently. The
     report emits a caveat when `tax_year` is `None` — do not ignore it.
2. **Validate the log** (each condition raises, none is absorbed):
   - `instrument_type` outside `SPOT_FOREX` / `CURRENCY_FUTURES` / `FORWARDS`.
   - Duplicate `trade_id` (double-counted PnL).
   - Non-finite realized or mark-to-market PnL.
   - Non-zero `unrealized_mtm_pnl_usd` on a position not open at year end —
     mark-to-market applies only to positions held on the last business day
     (§ 1256(a)(1)).
   - `trade_date` not parseable as ISO `YYYY-MM-DD` when filtering.
3. **Determine § 1256 eligibility per contract** (input, never inferred):
   - `sec1256_eligible=True` only on a professional determination under
     § 1256(g)(2). `None` is treated as ineligible and warns.
   - A `SPOT_FOREX` record asserted eligible produces an explicit warning: spot
     is not a § 988(c)(1)(B)(iii) contract, so no § 988(a)(1)(B) election exists.
   - No eligible position at all → recommendation `INSUFFICIENT_ELIGIBILITY_BASIS`;
     no 60/40 comparison is presented.
4. **§ 988 ordinary scenario**:
   - Tax = realized PnL × ordinary rate. Year-end unrealized PnL is excluded:
     § 988 has no mark-to-market regime.
   - Losses are ordinary and escape the § 1211(b) cap, subject to limits the
     engine does not model (see standards, "Out of scope").
5. **§ 1256 60/40 scenario** (eligible positions only):
   - PnL = realized + year-end mark-to-market (§ 1256(a)(1)).
   - Blended rate = 0.60 × LTCG + 0.40 × STCG (§ 1256(a)(3)).
6. **Loss waterfall for a net § 1256 loss** — in this order:
   - **§ 1212(c)** carryback to each of the 3 preceding years, earliest first,
     capped at net § 1256 gain in those years, 60/40 character preserved.
     Elected on Form 6781 box D, amount on line 6.
   - **§ 1211(b)** offset against other current-year capital gains.
   - **§ 1211(b)(1)** up to $3,000, or $1,500 married filing separately, against
     ordinary income.
   - **§ 1212(b)** remainder carries forward indefinitely. Reported, but given no
     current-year value — it is deferred, not forfeited.
7. **Compare, then read the caveats**:
   - `ELECT_SECTION_1256` / `REMAIN_SECTION_988` / `INSUFFICIENT_ELIGIBILITY_BASIS`.
   - `eligibility_warnings` and `caveats` are part of the output, not decoration.
8. **Loss year on currency futures — consider the reverse election**:
   - § 988(c)(1)(D)(ii) permits electing *into* § 988 ordinary treatment for
     regulated futures contracts and nonequity options otherwise under § 1256,
     converting capital losses to ordinary. Not modelled by the engine.
9. **Preserve the record**: the election is per transaction and must be
   identified in books and records on the trade date (Treas. Reg. § 1.988-3(b)(3))
   with a verification statement attached to the return (§ 1.988-3(b)(4)).
