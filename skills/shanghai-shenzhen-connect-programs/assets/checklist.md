# Pre-Flight Checklist — Northbound Stock Connect

## Start of day
- [ ] Is today a Northbound trading day — **both** the Hong Kong and Mainland markets open, not just the Mainland?
- [ ] Are the SEHK eligible-security and **sell-only** lists refreshed for today?
- [ ] Is the previous closing price loaded for every tradable security (it anchors both price limits all day)?
- [ ] Is the **market-open** shareholding position loaded per selling participant / SPSA Investor ID?
- [ ] Has the Daily Quota been reset to RMB 52 billion per channel, with no carry-over from yesterday?
- [ ] Has yesterday's buying suspension latch been cleared?

## Order screening
- [ ] Is the order routed on the channel matching its listing venue (SSE → Shanghai Connect, SZSE → Shenzhen Connect)?
- [ ] Are market orders rejected outright — only limit orders are accepted Northbound, all day?
- [ ] Is the board lot applied to **buys only**, so odd-lot sells stay possible?
- [ ] Is the STAR exception implemented — board lot 1 share, minimum order 200 shares?
- [ ] Does ChiNext still use the **100-share** board lot (only STAR is excepted)?
- [ ] Is the maximum order size enforced on both sides — 1,000,000 shares, 300,000 ChiNext, 100,000 STAR?
- [ ] Is the tick size checked — RMB 0.01 for A shares, RMB 0.001 for ETFs?
- [ ] Is the price limit ±10% Main Board, ±20% STAR and ChiNext, off the previous close?
- [ ] Is the ETF price-limit percentage taken from reference data rather than defaulted (±10% normally, ±20% for a published set)?
- [ ] Are prices held as decimals, never binary floats, so the tick check is exact?

## Buy gates
- [ ] Are buys blocked on sell-only securities while sells stay open?
- [ ] Is Northbound buying suspended at **28%** aggregate foreign shareholding and resumed only at **26%** (hysteresis, not one threshold)?
- [ ] Is the order that **exhausts** the quota accepted, with only the *next* one refused?
- [ ] Can the Daily Quota Balance go **negative** — is "the Daily Quota is exceeded" a reachable state?
- [ ] Does exhaustion during a continuous or closing call auction latch for the **remainder of the day**?
- [ ] Does exhaustion during the **opening call auction** *not* latch, so cancellations can restore buying?
- [ ] Is the latch immune to a later sell trade restoring the balance?

## Sell gates
- [ ] Is T+1 enforced by the **market-open position**, not by comparing a purchase date to today?
- [ ] Is the check against the day's **cumulative** sell quantity, so a position cannot be sold twice?
- [ ] Does a security with no recorded opening position reject the sell, rather than pass it?
- [ ] Are sells accepted regardless of quota balance, including when buying is suspended?

## Quota accounting
- [ ] Is the balance maintained as `Daily Quota – Buy Orders + Sell Trades + Adjustments`?
- [ ] Is quota consumed at **buy order** submission, not at buy fill?
- [ ] Is quota restored at **sell trade** execution, not at sell order acceptance?
- [ ] Does cancelling a buy release only the **unfilled** notional?
- [ ] Is the balance left **uncapped**, so a net-sell day can lift it above the Daily Quota?
- [ ] Is the computed balance reconciled against SEHK's dissemination — remembering it only publishes a figure below 30%?

## Sign-off
- [ ] Is every rejection auditable with a stable code and the rule it enforces?
- [ ] Does every missing input (unregistered security, absent opening position, unknown board) **reject** rather than pass?
- [ ] Are STAR and ChiNext orders restricted to institutional professional investors?
- [ ] Is the client-side gate documented as a pre-check, with SSE/SZSE/CSC remaining authoritative?
- [ ] Is engine state confined to one thread, or externally serialised?
