---
name: lme-london-metal-exchange-integration
description: >-
  Client-side pre-dispatch validation for London Metal Exchange outright orders —
  per-metal lot tonnage (Copper/Aluminium 25 MT, Nickel 6 MT, Tin 5 MT), the per-metal
  LMEselect outright tick ($0.50/MT, but $5.00/MT for Nickel and Tin), the symmetric
  Daily Price Limit band around the previous 3-month Closing Price, and prompt-date
  structure and tenor limits.
domain: Global Market Integration & FX
subdomain: Commodity Futures & LME Connectivity
tags: ["lme", "london-metal-exchange", "lmeselect", "prompt-dates", "daily-price-limits", "tick-sizes", "base-metals", "3m-benchmark"]
brokers_frameworks: ["LMEselect", "LMEselect FIX Order Entry Gateway", "LME Rulebook", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when building or auditing an order path into the **London Metal
Exchange** for outright base metal futures — Copper (`CA`), Primary Aluminium
(`AH`), Aluminium Alloy (`AA`), NASAAC (`NA`), Standard Lead (`PB`), Primary
Nickel (`NI`), Tin (`SN`), Special High Grade Zinc (`ZS`). It covers the checks
that belong on the client side, before a message leaves your process:

- How much metal is this order actually for, in tonnes?
- Is the price on **this metal's** outright tick?
- Is the price inside today's **Daily Price Limit** band, which the LME will
  refuse orders outside of?
- Is the prompt date one the contract actually lists?

The LME breaks three assumptions that hold on CME and ICE, and each one has its
own failure mode:

| Assumption | LME reality |
|---|---|
| One lot size per exchange | **Per metal, in tonnes.** 25 MT for Cu/Al/Pb/Zn, 20 MT for AA/NASAAC, **6 MT for Nickel**, **5 MT for Tin**. |
| One tick size per exchange | **Per metal.** $0.50/MT for most, but **$5.00/MT for Nickel and Tin** outrights on LMEselect and in the Ring. |
| Monthly expiries | **Prompt dates.** Daily out to 3 months, weekly (Wednesdays) 3–6 months, monthly (third Wednesday) beyond — out to 123 months for Cu/Al but only **15 months for Tin**. |

## When NOT to Use

- **Not a transport.** Nothing here opens a socket, logs on to LMEselect, or
  sends an order. `ready_to_send` means "passed the checks modelled here", never
  "the LME has the order". Session management, conformance, throttles and
  recovery are out of scope.
- **Not a prompt-date calendar.** This module does not ship the LME trading
  calendar and cannot tell you that a given Wednesday is tradeable. Cash is two
  business days forward and 3M is three calendar months forward, both resolved
  against the LME business-day calendar, and the LME issues notices with
  **substitute prompt dates** around holidays. Pass `valid_prompt_dates` to make
  the check authoritative; without it the module flags structure and stays
  explicit that it has not confirmed the date.
- **Not a complete pre-trade control set.** LMEselect also enforces Dynamic and
  Static Price Bands, Exchange-set and Member-set maximum order size limits (in
  lots and in notional, via LMEptrm), and an order-entry throttle. None are
  modelled here. Passing these checks is not a prediction that LMEselect accepts
  the order.
- **Not for carries, options, or TAPOs.** Scope is outrights. Carries (calendar
  spreads) trade on a different and smaller tick, large-tick electronic calendar
  spreads on a third tick again since January 2026, and Daily Price Limits apply
  to outrights only.
- **Not a reference-data service.** The bundled catalog is a snapshot retrieved
  2026-08-25. The LME revises tick sizes and Daily Price Limits **by notice** —
  Lead and Zinc moved from 15% to 12% as recently as 8 June 2026. Every spec
  carries `specs_source` and `specs_as_of`; refresh them before relying on them.
- **Not a margin, position-limit or delivery tool.** Warrants, LMEsword, the
  physical delivery and warehousing rules, and position reporting are separate
  surfaces.

## Prerequisites

- Per-metal LME reference data: contract code, lot size in tonnes, outright tick
  size, Daily Price Limit percentage, and the furthest listed monthly prompt.
- The previous business day's **Closing Price for the 3-month contract** — the
  reference the Daily Price Limit is measured from.
- The LME trading calendar and current substitute-prompt-date notices, if you
  intend to confirm explicit prompt dates.
- Python 3.9+. Standard library only (`decimal`, `dataclasses`, `datetime`,
  `logging`).

## Workflow

1. **Resolve the metal by its LME contract code, and confirm the lot size is
   this metal's.** The codes are not mnemonic and the tonnages differ: `AH` is
   Primary Aluminium at 25 MT but `AA` is Aluminium Alloy at 20 MT; `NI` is 6 MT
   and `SN` is 5 MT. Total tonnage is `lots × lot_size_mt`, and it is the
   quantity that matters for exposure — not the lot count.
2. **Check the price against this metal's outright tick, in decimal
   arithmetic.** Nickel and Tin are $5.00/MT. A universal $0.50 constant accepts
   nine Nickel prices in ten that LMEselect refuses. Check positivity
   separately: the remainder of a negative price against a positive tick is
   zero, so a negative price passes a tick test on its own.
3. **Reject a prompt date the contract does not list.** Tin lists monthly
   prompts to 15 months, Aluminium Alloy and NASAAC to 27, Lead, Zinc and Nickel
   to 63, Copper and Aluminium to 123. A date past the trade date is not enough.
4. **Treat a structural prompt mismatch as a question, not a refusal.** Weekly
   prompts normally fall on a Wednesday and monthly prompts on the third
   Wednesday, but the LME publishes substitute dates around holidays. Flag the
   mismatch and confirm against the calendar; hard-rejecting every non-Wednesday
   refuses legitimate substitute prompts.
5. **Run the Daily Price Limit against the previous 3-month Closing Price — and
   symmetrically.** The band is `close × (1 ± pct)`: 12% for Aluminium, Copper,
   Lead and Zinc; 15% for Nickel, Tin, Aluminium Alloy and NASAAC. The LME
   accepts **no bid above the upper limit and no offer below the lower one**,
   and it refuses a deep passive bid below the lower limit too. This is not
   ICE's directional Reasonability Limit — do not port that logic here.
6. **Fail closed when the reference price is missing.** The DPL is a real
   order-entry rejection. An unchecked DPL is `NO_DPL_REFERENCE_PRICE` and
   `ready_to_send` False, not a pass. Do not substitute the mid, the top of
   book, or the LME Official Price — the reference is the previous business
   day's **Closing** Price for the 3-month contract.
7. **Report the notional in exact decimal money.** `total_tonnage_mt ×
   price_usd_per_mt`, quantized to cents. LME base metals are quoted in US
   dollars per tonne, so the notional is USD.

> Full procedure: see `references/workflows.md`.
> Rule citations and published levels: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming a universal $0.50/MT tick.** This is the headline error and it is
  silent: Nickel and Tin outrights are **$5.00/MT**. A gateway that validates
  Nickel at $16,500.50 passes it locally and collects an LMEselect rejection —
  or, worse, reprices around a tick grid that does not exist.
- **Assuming 25 MT per lot.** Nickel is 6 MT. Sizing 10 lots of Nickel as 250 MT
  instead of 60 MT is a **4.17×** over-position. Aluminium Alloy and NASAAC are
  20 MT, not the 25 MT their aluminium name suggests.
- **Applying the Daily Price Limit directionally.** ICE's Reasonability Limit
  refuses a buy above the upper limit and a sell below the lower, and accepts
  deep passive orders on the far side. The LME band is symmetric on price: an
  order outside it is refused **whichever side it is**. Copying the ICE logic
  lets through orders the LME rejects.
- **Measuring the Daily Price Limit from the wrong price.** It is the previous
  business day's Closing Price for the **3-month** contract, applied equally to
  every prompt on the curve. Not the mid, not the top of book, not the LME
  Official Price (which is set in the Ring), and not the current 3M.
- **Treating LME prompts as monthly expiries.** Hard-coding a third-Wednesday
  expiry drops the daily prompts out to 3 months that are the whole point of the
  LME's structure for a physical hedger, and quietly mis-books cash and tom.
- **Hard-rejecting a non-Wednesday prompt.** The LME issues substitute prompt
  dates around bank holidays. A strict weekday rule rejects tradeable dates.
- **Assuming every metal has the same curve length.** Tin lists monthly prompts
  to 15 months. A 60-month tin prompt is not a far date — it does not exist.
- **Caching tick sizes and Daily Price Limits as constants.** Both are revised
  by LME notice; Lead and Zinc moved to 12% on 8 June 2026. Carry the source and
  retrieval date with the value.
- **Reading a local pass as an execution guarantee.** Price Bands, Exchange- and
  Member-set maximum order size limits, and the order throttle can all still
  reject, and the DPL Multiple Day Framework can suspend a metal entirely after
  three consecutive limit days in the same direction.
- **Retrying an order because the request timed out.** LMEselect may already
  have it. Resolve the order's state through the venue and reuse the original
  client order ID; a retry under a fresh identifier is a second position. See
  `order-placement-idempotency`.

## Verification

- Lot tonnage reproduces the published specifications independently: 10 lots of
  `CA` ⟹ 250 MT; 10 lots of `NI` ⟹ **60 MT**; 7 lots of `SN` ⟹ 35 MT; 3 lots of
  `AA` ⟹ 60 MT.
- Notional is exact: `CA` 10 lots at $9,250.50/MT ⟹ 250 × 9,250.50 =
  **$2,312,625.00**; `NI` 10 lots at $16,500.00/MT ⟹ **$990,000.00**.
- `NI` at $16,500.50/MT ⟹ `INVALID_TICK_SIZE` — a whole number of $0.50 steps,
  but not of $5.00. This is the regression a universal $0.50 tick introduces.
  `NI` at $16,505.00/MT ⟹ passes. `CA` at $9,250.23/MT ⟹ `INVALID_TICK_SIZE`.
- `CA` against a $9,200.00 3-month close ⟹ band $8,096.00–$10,304.00 (12%).
  $10,304.00 passes and $10,304.50 is refused; $8,096.00 passes and $8,095.50 is
  refused. **$8,000.00 is refused as a BUY *and* as a SELL** — the regression a
  directional band introduces. `NI` against $16,400.00 ⟹ $13,940.00–$18,860.00
  (15%).
- Omitting `previous_close_3m_usd` ⟹ `NO_DPL_REFERENCE_PRICE`, `ready_to_send`
  False.
- A 2031 prompt on `SN` ⟹ `INVALID_PROMPT_DATE`; the same date on `CA` ⟹ passes.
  A prompt on or before the trade date ⟹ `INVALID_PROMPT_DATE`.
- A monthly prompt that is not a third Wednesday ⟹ passes with a warning and
  `prompt_date_confirmed` False, not a rejection. Supplying `valid_prompt_dates`
  ⟹ `prompt_date_confirmed` True, and a date outside the set is refused.
- `lots` of `0`, `-10`, `1.5`, `"10"` or `True`; `side` of `"BANANA"`; a
  negative, zero, `NaN` or `Inf` price; a `prompt_date` of `"not-a-date"` ⟹
  `ValueError`/`TypeError`, never an approved order.
- Run `python -m unittest discover -s skills/lme-london-metal-exchange-integration/scripts`
  and confirm a 100% pass rate.
- Against conformance only: submit one validated order and confirm LMEselect
  accepts the instrument identification and price. A symbology or prompt-date
  error that unit tests cannot see is one where the gateway disagrees with your
  reference data.

## Related Skills

- `ice-futures-us-eu-integration`
- `exchange-tick-size-regime-tracking`
- `global-exchange-holiday-calendar-handling`
- `futures-contract-roll-automation`
- `commodity-futures-storage-and-carry-cost-modeling`
- `physical-vs-cash-settlement-handling`
- `order-placement-idempotency`
