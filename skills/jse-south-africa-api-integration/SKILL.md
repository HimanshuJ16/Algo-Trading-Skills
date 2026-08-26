---
name: jse-south-africa-api-integration
description: >-
  Quantitative market gateway engine for the Johannesburg Stock Exchange equity market (JSE, LSEG Millennium Exchange), enforcing ZAC South African Cents pricing on a flat 1-cent tick, whole-share lots up to the 99,999,999 Maximum Order Size, the ZA01 +/-90% price band, and per-segment/per-session circuit breaker tolerances.
domain: Global Market Integration & FX
subdomain: African Market Connectivity & JSE Gateway
tags: ["jse", "south-africa", "johannesburg-stock-exchange", "zac-cents", "millennium-exchange", "circuit-breakers", "price-bands"]
brokers_frameworks: ["JSE Millennium Exchange (LSEG Technology)", "JSE Trading Gateway (FIX 5.0 SP2)", "JSE Native Trading Gateway", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when routing equity orders to the Johannesburg Stock Exchange (JSE), whose equity market runs on the LSEG Millennium Exchange platform. Order entry requires: prices in **ZAC (South African Cents)**, where ZAR 1 = 100 ZAC; whole-cent prices on a tick size of **1 for every instrument**; whole-share quantities on a **lot size of 1** up to the **99,999,999 Maximum Order Size**; and awareness of the two distinct price controls the JSE operates — **price bands**, which reject orders at entry, and **circuit breakers**, which do not.

## When NOT to Use

- **As a substitute for exchange-side controls**: this is a client-side pre-trade filter. The JSE trading system remains authoritative and may reject or expire an order this engine approves. Halts, suspensions, session state, entitlements and member-level risk limits are not modelled.
- **Non-equity JSE markets**: equity derivatives, currency derivatives, commodity derivatives and bonds have their own contract specifications, tick conventions and circuit breaker tables. The tolerances encoded here are the **Equity Market (EQM)** table only.
- **As a fill predictor**: a circuit breaker assessment says whether a trade at the order's limit price *would* breach a tolerance. It does not predict whether the order trades at all.
- **Off-book (reported) trades**: price bands do not restrict the execution of off-book trades. Applying the band to a reported trade produces a false rejection.

## Prerequisites

- JSE order request (`alpha_code`, `side`: `BUY`/`SELL`, `price_zac` in whole cents, `quantity` in shares, `reference_price_zac`).
- The **static reference price** in ZAC: the previous day's closing price or the last auction price. This anchors both the price band and the static circuit breaker — it is not the last traded price.
- Optionally the **dynamic reference price** (last traded price) in ZAC; without it the dynamic circuit breaker cannot be evaluated.
- The instrument's **trading segment** (`ZA01` Top Companies, `ZA02` Medium Liquid, `ZA03` Less Liquid, `ZA04` Specialist Products, `ZA06` Exchange Traded Products, `ZA11`/`ZA12` NSX) and the **trading session** the order is entered for. Both come from the JSE reference data distributed daily.
- The current JSE circuit breaker and price band schedules — see `references/standards.md` for the dated tables in force.

## Workflow

1. **Alpha Code Normalization**:
   - Uppercase and validate the JSE alpha code (the instrument `Symbol`). Codes are **alphanumeric and not uniformly three letters** — `S32` (South32) carries digits and ETP codes such as `ETFSWX` are six characters. Enforce the character class, not a fixed length.
2. **Structural Validation — fail loudly before any arithmetic**:
   - Validate side, segment and session against the published enumerations.
   - Validate `quantity` as a whole number greater than zero.
   - Validate `reference_price_zac` as finite and strictly positive **before** using it as a divisor or band anchor. A zero or missing previous close must raise, never silently produce a zero-width band.
3. **ZAC Tick Alignment**:
   - The JSE tick size is $1\text{ ZAC}$ for **every** instrument at every price level; there is no price-tiered tick ladder. A valid limit price is therefore simply a whole number of cents greater than zero. Check it with integer arithmetic, not a float tolerance.
   - The $0.5\text{ ZAC}$ half-tick in JSE documentation is the price improvement the *system* applies to pegged hidden order executions — not a price a member may submit.
4. **Order Size Check**: reject quantities above the Maximum Order Size of $99{,}999{,}999$ shares.
5. **Price Band Audit — the only exchange price control that rejects**:
   - `ZA01` instruments are subject to a price band of $\pm 90\%$ of the **static** reference price; orders outside it are rejected by the trading system on entry.
   - No band is published for the other segments. Do **not** invent one — enforcing a narrower band client-side rejects orders the JSE would accept.
6. **Circuit Breaker Assessment — a warning, never a rejection**:
   - Look up the (static, dynamic) tolerance for this **segment and session** pair; several pairs have no breaker at all.
   - A breach is `>=` the tolerance ("equal or greater than that permitted"), evaluated against both the static reference price and the last traded price, the more restrictive taking precedence.
   - A breach moves the instrument into a 5-minute Volatility Auction Call session. The aggressing order's remainder is added to the book if its time in force is persistent, and expired otherwise — an IOC or Market order expires with `Expired (circuit breaker breached)`. The order is still *valid*; classify it as a market-impact warning, not a rejection.
7. **ZAR Notional Conversion**: $\text{Notional}_{\text{ZAR}} = \frac{\text{price\_zac} \times \text{quantity}}{100}$.
8. **Audit Report Generation**: Output structured `JseOrderReport` and branch on `is_rejected`, not on string equality with a status.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Inventing a tiered tick ladder**: the JSE tick is 1 ZAC for every instrument at every price. A client-side rule such as "5 ZAC above ZAR 100" rejects perfectly legal prices — 85,502 ZAC on Naspers is valid — and silently blocks orders at the top of the book on the most liquid names in the market.
- **Submitting prices in ZAR instead of ZAC**: ZAR 300 instead of 30,000 ZAC is a 100x pricing error. On a `ZA01` name it lands far outside the $\pm 90\%$ price band and is rejected outright; on a segment with no band it can rest as a wildly mispriced order.
- **Treating a circuit breaker as an order rejection**: a breach triggers a volatility auction, it does not reject the order. Cancelling and re-submitting on a "rejection" that never happened duplicates exposure — the original order may still be resting in the book after the auction.
- **Treating a price band as a circuit breaker**: the band is $\pm 90\%$ and rejects on entry; the ZA01 continuous-trading breaker is 10%/3% and triggers an auction. They are different mechanisms with different consequences, an order of magnitude apart.
- **Using the last traded price as the static reference**: the static reference price is the previous close or the last auction price. Feeding the last traded price into the price band and the static breaker mis-anchors both, in opposite directions on a trending day.
- **Applying one circuit breaker tolerance market-wide**: tolerances are defined per segment *and* per session — ZA01 continuous trading is 10%/3%, its closing auction 4%/2%, and ZA03 is 50%/25% throughout. A single hard-coded percentage is wrong nearly everywhere.
- **Assuming a missing dynamic reference means "no breach"**: with no last traded price, the dynamic breaker cannot be evaluated. Record that it was skipped rather than reporting a pass.
- **Assuming three-letter tickers**: `S32` contains digits and ETP codes run longer. A letters-only, exactly-three rule rejects a Top 40 constituent.
- **Unvalidated reference price**: a zero or missing previous close divides by zero or collapses the band. Raise on it; never fall back to the order price.

## Verification

- Instantiate `JseSouthAfricaApiEngine`. Route a Naspers order (`alpha_code="NPN"`, `price_zac=85_500`, `quantity=100`, `reference_price_zac=85_500`, segment `ZA01`, session `CONTINUOUS_TRADING`) and verify the tick is reported as **1 ZAC**, the ZAR equivalent as ZAR 855.00, the notional as ZAR 85,500.00, and the status as `JSE_ORDER_VALIDATED` with `is_rejected` false.
- Confirm the tick regression: `price_zac=85_502` against the same reference must be **accepted** (whole cents, tick 1), while `85_500.5` and `85_500.0001` must return `INVALID_TICK_SIZE`.
- Confirm the two price controls are distinct: against a 10,000 ZAC static reference on `ZA01`, a 15,000 ZAC order (+50%) returns `VOLATILITY_AUCTION_RISK` with `is_rejected` **false**, while a 20,000 ZAC order (+100%) returns `PRICE_BAND_BREACH` with `is_rejected` **true**.
- Confirm `S32` validates as an alpha code and that a zero `reference_price_zac` raises rather than dividing by zero.
- Run the test suite:
```bash
cd skills/jse-south-africa-api-integration/scripts
python -m unittest test_jse_south_africa_api_integration.py
```

## Related Skills

- `exchange-tick-size-regime-tracking`
- `exchange-self-match-prevention-configuration`
- `currency-pair-quoting-convention-normalization`
- `multi-currency-pnl-and-fx-conversion`
