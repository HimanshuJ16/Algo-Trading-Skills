# Workflows for Shanghai-Shenzhen Connect (Northbound Trading)

Rule citations are to the HKEX *Information Book for Investors* (6 July 2026);
see `references/standards.md` for the quoted text behind each step.

## 1. Start of day

1. **Confirm it is a Northbound trading day.** Northbound trades only when *both*
   the Hong Kong and Mainland markets are open (§3.6). The Mainland calendar
   alone is not the trigger — a Mainland trading day falling on a Hong Kong
   public holiday is closed for Northbound.
2. **Ingest the eligible-security and sell-only lists** published by SEHK. These
   are data, not derivable from the stock code. A security moved to sell-only
   remains sellable but must be blocked from buying.
3. **Load the previous closing price** for every security you may trade. It
   anchors both price limits for the whole day; a stale close shifts the whole
   band.
4. **Load the market-open shareholding position** per security, per selling
   participant (or per SPSA Investor ID). This is the pre-trade checking
   baseline, the same snapshot CCASS replicates to CSC (§3.19).
5. **Reset the Daily Quota** to RMB 52 billion for each channel and clear the
   previous day's buying suspension. Unused quota does not carry over (§3.4).

## 2. Order screening, before anything is sent to CSC

Run in this order, so the rejection names the primary defect rather than an
incidental one:

1. **Reference data present?** No previous close, no board, no eligibility flag
   means the order cannot be validated. Reject — do not send it and let CSC
   decide.
2. **Channel matches the listing venue?** An SSE security cannot route over
   Shenzhen Connect. The two channels hold separate RMB 52bn quotas, so a
   mis-routed order debits the wrong pool.
3. **Limit order?** Only limit orders are accepted Northbound, throughout the
   day (§3.8). Reject market orders rather than converting them.
4. **Order size.** Maximum 1,000,000 shares — 300,000 on SZSE ChiNext, 100,000
   on SSE STAR — on both sides. For **buys only**: board lot of 100 shares,
   except STAR, whose board lot is 1 share with a 200-share minimum (§3.11).
   - **Decision point — do not apply the board lot to sells.** Odd lots are
     sellable, and must be sold in a single order. A gate that rounds or rejects
     odd-lot sells permanently strands corporate-action remnants.
   - **Decision point — ChiNext keeps the 100-share lot.** It shares STAR's ±20%
     price limit and its professional-investor restriction, but not its board
     lot.
5. **Tick size.** RMB 0.01 for A shares, RMB 0.001 for ETFs (§3.11).
6. **Price limit.** ±10% off the previous close on the SSE/SZSE Main Boards,
   ±20% on STAR and ChiNext. ETFs are ±10% normally and ±20% for a published
   set, so take the ETF percentage from reference data rather than defaulting it
   (§3.9).

## 3. Buy-side gates

1. **Sell-only designation** — buying blocked, selling unaffected (§3.1–3.2).
2. **Foreign shareholding** — once aggregate foreign shareholding of the security
   reaches 28%, Northbound buy orders are not allowed until it is sold down to
   26% (§3.20).
   - **Decision point — the thresholds are asymmetric on purpose.** Resuming at
     28% instead of 26% makes buying flap across the boundary.
3. **Daily Quota.**
   - If Northbound buying is already suspended for the day, reject.
   - If the balance is already at or below zero, reject.
   - Otherwise **accept, then deduct the full order notional.**
   - **Decision point — the order that exhausts the quota is accepted.** The rule
     blocks the buy order that arrives *after* the balance is gone, not the one
     that consumes the last of it. That is why HKEX describes the Daily Quota
     being "exceeded" at all. Rejecting on `balance < notional` refuses orders
     SEHK would take and makes the negative balance unreachable.
   - **Decision point — exhaustion latches, except in the opening call auction.**
     In a continuous auction or the closing call auction, exhaustion stops
     Northbound buying for the remainder of the day, and a later sell trade
     restoring the balance does not lift it. In the opening call auction it does
     not latch: cancellations are common there and SEHK resumes accepting buys
     if the balance returns positive before the auction ends (§3.4).

## 4. Sell-side gate

1. **Pre-trade checking.** Reject if the day's cumulative sell quantity would
   exceed the market-open shareholding position (§3.19).
   - **Decision point — this is what enforces T+1, not a date comparison.**
     Shares bought today are absent from the market-open snapshot, so they are
     unsellable today by construction. A `purchase_date == today` check enforces
     something weaker and catches no overselling at all.
   - **Decision point — no position on record means zero, not unlimited.** A sell
     in a security you have no opening position for must be rejected.
2. **Never gate a sell on quota.** "Investors are always allowed to sell their
   cross-boundary securities regardless of the quota balance" (§3.4).

## 5. Order lifecycle and quota accounting

Maintain the balance as the published identity, not as a running counter:

```
Daily Quota Balance = Daily Quota - Buy Orders + Sell Trades + Adjustments
```

| Event | Quota effect | Pre-trade headroom effect |
|---|---|---|
| Buy order accepted | Deduct full notional | — |
| Buy order fills (partial or full) | **None** — already deducted at submission | — |
| Buy order cancelled | Credit back the **unfilled** notional only | — |
| Sell order accepted | **None** | Deduct full quantity |
| Sell trade (fill) | Credit filled notional | — (already committed) |
| Sell order cancelled | None | Credit back the unfilled quantity |

- **Decision point — consumption is at order time, restoration at trade time.**
  The formula's terms are "Buy Orders" and "Sell Trades". Crediting quota when a
  sell order is *accepted* manufactures buying power SEHK never granted; the
  order may never fill.
- Reconcile your computed balance against SEHK's disseminated figure. Note that
  the real balance is only published once it falls below 30% of the quota —
  above that it reads "Available", so intraday reconciliation is only possible
  in the range where it matters.

## 6. Checks this workflow deliberately leaves to CSC

- **Dynamic price check** (§3.10): a buy priced more than a prescribed percentage
  (3% at the initial phase) below the current best bid is rejected by CSC. It
  needs the live best bid and is applied venue-side.
- **Final eligibility, price limit and pre-trade checking**: SSE, SZSE and CSC
  are authoritative. A client-side gate reduces round trips and produces an audit
  trail; it never substitutes for the venue's own answer.
