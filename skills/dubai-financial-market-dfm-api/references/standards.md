# Standards for Dubai Financial Market (DFM) API Integration

## Verified requirements

| Metric | Engineering Standard | Basis |
|---|---|---|
| Investor Number (NIN) | Orders must carry a valid **10-digit** National Investor Number, issued by Dubai CSD at DFM. A NIN is required for an investor to hold DFM-traded shares. | DFM investor services / NIN request documentation. |
| Tick size authority | Tick sizes are **not fixed in the rulebook**: "the Market shall specify minimum price units or tick sizes for each relevant kind of Security and Derivative by way of Circular." | DFM Module Three, Rule 16.17(a)(ii). |
| Tick structure (eff. 06 Apr 2026) | `<1 → 0.001` · `1–9.99 → 0.01` · `10–49.98 → 0.02` · `50–99.95 → 0.05` · `100 and above → 0.10`. Applies to all listed equities, ETFs and REITs (boards 505, 510, 520). Non-compliant open orders are rejected or must be amended. | DFM circular effective 6 April 2026. |
| Price limit authority | Upper and Lower Price Limits are **per security, set by Circular** — not a universal symmetric band: "If any Order ... causes the price of any listed Security to exceed the Upper and Lower Price Limits set by the Market by Circular for the specific listed Security, the Market shall be permitted to prevent the Order from entering the Order Book." | DFM Module Three, Rule 16.16(a). |
| Equity band (commonly reported) | **10% limit down / 15% limit up.** The 10% limit down was reinstated for the session of 28 February 2021 after a temporary COVID-era reduction to 5%, "while the 15% limit up cap kept unchanged". Treat as a default to override per security, not as authority. | Emirates NBD Securities circular (broker source). |
| First trading session | Upper and Lower Price Limits **do not apply** to a listed Security in its first Trading Session; the price floats until a transaction executes, and that session's Closing Price becomes the next session's benchmark. | DFM Module Three, Rule 16.16(c). |
| Dual-listed securities | For a dual-listed Issuer with a principal listing on a Foreign Market, the limits are calculated by reference to the **Foreign Market's** closing price for the previous Trading Session. | DFM Module Three, Rule 16.16(d). |
| Trading session | Continuous trading 10:00–14:45 GST, pre-open from 09:30, Monday to Friday. This module performs no session-time validation. | DFM/exchange schedule sources. |
| Currency | DFM securities are AED-denominated. **Nasdaq Dubai is a separate market** listing both USD-denominated (board 200) and AED-denominated (board 210) equity products, with separate USD and AED settlement calendars. | Nasdaq Dubai / Clearstream market infrastructure documentation. |

## Explicitly NOT verified

- **FIX 4.4 as DFM's order-entry protocol.** The string "FIX" does not appear anywhere in DFM's 78-page Membership, Trading and Derivatives Rules (Module Three), and DFM member connectivity specifications are not publicly available. The FIX framing this module emits conforms to the published FIX 4.4 standard, but the assertion that DFM accepts FIX 4.4 is unverified.
- **NIN carried in FIX Tag 1 (`Account`).** Plausible but unconfirmed; no public DFM source maps the NIN to a specific FIX tag. Confirm against the member technical specification.
- **`SenderCompID`/`TargetCompID` values.** The defaults are placeholders with no DFM significance.

Do not present these three as facts in downstream work. They are illustrative defaults.

## Sources

- DFM, *Membership, Trading and Derivatives Rules (Module Three)* — Rules 16.16 (Trading safeguards / Upper and Lower Price Limits) and 16.17 (Order values / Minimum Price Unit).
  https://assets.dfm.ae/docs/default-source/market-rules/dfm---membership-trading-and-derivatives-rules-module---english.pdf
- DFM Market Rules index. https://www.dfm.ae/the-exchange/regulation/market-rules
- DFM tick size revision effective 6 April 2026 (full bracket table incl. AED 100+ → 0.10):
  https://www.emiratesnbdsecurities.com/en/investor-updates/revision-to-tick-size-structure
  and https://www.voiceofemirates.com/en/business/2026/03/31/dubai-financial-market-announces-revision-to-minimum-tick-size-for-listed-securities-effective-april-6-2026/
- Reinstatement of the 10% limit down, 15% limit up unchanged (28 Feb 2021):
  https://www.emiratesnbdsecurities.com/en/investor-updates/reinstates-limit-down-on-prices-of-listed-securities
- Nasdaq Dubai USD/AED denominated boards and settlement:
  https://www.clearstream.com/clearstream-en/res-library/market-coverage/market-infrastructure-united-arab-emirates-nasdaq-dubai--1281490
- DFM investor services (NIN). https://www.dfm.ae/investing/services/investor-faqs
