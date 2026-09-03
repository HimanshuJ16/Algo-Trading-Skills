---
name: dubai-financial-market-dfm-api
description: >-
  Use when submitting orders to Dubai Financial Market over FIX 4.4, validating the
  10-digit National Investor Number, the tick size structure and per-security price
  limits before dispatch under UAE SCA rules.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: global-market-integration
  tags: dfm, dubai-financial-market, gcc-markets, fix-4.4, nin-investor-number, aed-currency, mena-trading
  brokers_frameworks: "DFM FIX 4.4 Gateway; Dubai CSD; Python Dataclasses"
  version: "1.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill in Middle Eastern (GCC/MENA) quantitative trading systems, broker execution engines, and regional market making algorithms submitting orders to the Dubai Financial Market (DFM). DFM operates under UAE SCA regulations. Orders require a 10-digit National Investor Number (NIN) issued by Dubai CSD at DFM, prices conforming to the tick structure in force, and prices inside the Upper/Lower Price Limits DFM sets per security.

## When NOT to Use

- **As a transport layer.** This module validates and builds a message; it opens no session and sends nothing. `is_accepted=True` means the order passed *local* checks, never that DFM received it.
- **As a substitute for DFM's member technical specification.** DFM's Membership, Trading and Derivatives Rules do not mention FIX at all, and DFM connectivity specs are not public. The field mapping here — notably NIN in Tag 1 — is an **unverified illustrative default** and must be confirmed with DFM/your broker before production use.
- **With hard-coded reference data.** Tick sizes (Rule 16.17(a)(ii)) and Upper/Lower Price Limits (Rule 16.16(a)) are both set **by DFM Circular, per security**. The defaults here are a starting point, not authority; source current values per instrument.
- **For Nasdaq Dubai without checking currency.** Nasdaq Dubai lists USD-denominated (board 200) *and* AED-denominated (board 210) equity products, and its trading manual is separate from DFM's. Do not assume AED.
- **For derivatives.** DFM futures carry different daily limits from cash equities (e.g. single-stock and index futures are published with wider bands); this module is written for listed securities.

## Prerequisites

- DFM session credentials (`SenderCompID`, `TargetCompID`) and the broker's member technical specification.
- National Investor Number (NIN) — a 10-digit number issued by Dubai CSD at DFM (e.g. `1099887766`).
- Instrument ticker symbol (e.g. `EMAAR` - Emaar Properties, `DEWA` - Dubai Electricity and Water).
- **Per-security reference data sourced from current DFM circulars**: the applicable tick band table and the Upper/Lower Price Limit percentages, plus the benchmark closing price.

## Workflow

1. **Investor NIN Validation**:
   - Verify a 10-digit numeric National Investor Number. The engine carries it in FIX Tag 1 (`Account`) — an unverified mapping, see *When NOT to Use*.
2. **Order Field Validation**:
   - Reject an unrecognised `side` outright. Never coerce it: defaulting an unknown side to SELL silently inverts the order.
   - Reject non-positive or non-integer quantity, non-finite or non-positive price, and any currency outside `AED`/`USD`.
3. **Tick Size Regime Audit** (structure effective 6 April 2026, all listed equities, ETFs and REITs):
   - $P < 1.00 \implies \text{Tick} = 0.001$.
   - $1.00 \le P < 10.00 \implies \text{Tick} = 0.01$.
   - $10.00 \le P < 50.00 \implies \text{Tick} = 0.02$.
   - $50.00 \le P < 100.00 \implies \text{Tick} = 0.05$.
   - $P \ge 100.00 \implies \text{Tick} = 0.10$.
   - Compare on integer tick counts, not `price % tick` — neither operand is exactly representable in binary floating point, so modulo misjudges prices at band edges.
4. **Upper/Lower Price Limit Check** (DFM Rule 16.16):
   - The band is **per security and asymmetric**. The commonly reported equity band is $-10\%$ limit down / $+15\%$ limit up — not $\pm 10\%$. Applying a symmetric $\pm10\%$ wrongly blocks legitimate orders between $+10\%$ and $+15\%$.
   - If the benchmark closing price is missing or non-positive, **reject** (`MISSING_REFERENCE_PRICE`). A missing benchmark must never mean "skip the band check".
   - Rule 16.16(c): a security in its **first Trading Session** floats with no limits — opt in explicitly via `is_first_trading_session`.
   - Rule 16.16(d): for a dual-listed issuer whose principal listing is on a Foreign Market, the benchmark is the **foreign market's** closing price — supply that as the benchmark rather than flagging the order unbanded.
5. **FIX 4.4 Message Construction**: Build an SOH-delimited New Order Single with BodyLength (tag 9) and CheckSum (tag 10) computed per the FIX 4.4 spec, and return a structured `DfmOrderExecutionReport`. Nothing is transmitted.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Omitting National Investor Number (NIN)**: Sending FIX order messages without a valid NIN in Tag 1, triggering immediate exchange gateway rejection.
- **Off-Tick Pricing in AED**: Submitting order price 7.855 AED on Emaar Properties, violating the 0.01 AED tick step for prices between 1.00 and 10.00 AED.
- **Trading Outside DFM Session Hours**: Attempting order entry outside the 10:00 - 14:45 GST continuous session (pre-open from 09:30; DFM trades Monday to Friday). This module performs **no** session-time check.
- **Omitting the AED 100+ Tick Bracket**: A four-tier table ending at "≥ 50 AED → 0.05" accepts 150.05 AED, which is a clean multiple of 0.05 but not of the 0.10 tick DFM requires at and above 100 AED. The order passes local validation and is then rejected by the exchange.
- **Assuming a Symmetric ±10% Band**: DFM's limit down and limit up differ. A symmetric ±10% check rejects a legitimate order at +12% — a silent loss of executable flow that looks like a working risk control.
- **Skipping the Band Check When the Benchmark Is Missing**: Guarding the price-band check with `if prior_close > 0` means absent reference data disables the control entirely and dispatches the order unchecked. Fail closed.
- **Coercing an Unknown Side to SELL**: `1 if side == "BUY" else 2` turns a typo, an empty string, or `"LONG"` into a sell order. Validate against an explicit allow-list.
- **Treating `price % tick == 0` as a Tick Check**: Binary floating point makes this unreliable at band edges; compare integer tick counts instead.
- **Passing Unsanitised Text into FIX Fields**: A `cl_ord_id` or `symbol` containing SOH or `=` forges extra FIX fields — an embedded `SOH10=000` injects a premature CheckSum and yields a malformed message. Reject those characters before framing.
- **Reading "built" as "sent"**: A returned payload is not an acknowledgement. Only a venue Execution Report confirms the order reached DFM.

## Verification

- Instantiate `DubaiFinancialMarketApiEngine`. Construct order for `EMAAR` (NIN = `1099887766`, Price = 7.85 AED, Qty = 10,000, benchmark = 7.80 AED). Verify NIN passes, the 0.01 AED tick applies, the price sits inside the band, and a FIX 4.4 message is built. Construct an off-tick price (7.855 AED) and verify `INVALID_TICK_SIZE`.
- Verify 150.05 AED is rejected (0.10 tick applies at and above 100 AED) while 150.10 AED is accepted.
- Verify 8.58 AED against a 7.80 benchmark is **accepted** (+10%, inside a +15% limit up) and 8.98 AED is rejected.
- Verify a missing benchmark returns `MISSING_REFERENCE_PRICE` rather than being accepted.
- Verify `side="LONG"` returns `INVALID_ORDER_FIELD` and builds no payload.
- Verify tag 9 and tag 10 in the raw message match a from-spec recomputation.
- Run `python -m unittest discover -s skills/dubai-financial-market-dfm-api/scripts`.

## Related Skills

- `exchange-tick-size-regime-tracking`
- `currency-pair-quoting-convention-normalization`
---
