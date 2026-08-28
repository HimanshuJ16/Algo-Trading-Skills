# Broker & Framework Coverage — pattern-day-trader-rule-compliance-us

All citations verified 2026-08-27 against the primary sources listed at the
bottom. Every threshold below is data with an as-of date, not a constant.

## Regulatory position

| Provision | Status | Substance |
|---|---|---|
| FINRA Rule 4210(f)(8)(B)(i) — "day trading" | **Deleted, effective 2026-06-04** | Purchasing and selling, or selling and purchasing, the same security on the same day in a margin account, *except* a long held overnight and sold the next day prior to any new purchase of the same security, or a short held overnight and purchased the next day prior to any new sale. |
| FINRA Rule 4210(f)(8)(B)(ii) — "pattern day trader" | **Deleted, effective 2026-06-04** | Four or more day trades within five business days; not applicable where day trades were 6 percent or less of total trades for the same five-business-day period. |
| FINRA Rule 4210(f)(8)(B)(iii) — day-trading buying power | **Deleted, effective 2026-06-04** | Prior day's equity less the (c) maintenance requirement, times four for equity securities. |
| FINRA Rule 4210(f)(8)(B)(iv)a — minimum equity | **Deleted, effective 2026-06-04** | $25,000, deposited before day trading continued and "maintained in the customer's account at all times". |
| FINRA Rule 4210(f)(8)(B)(iv)d — 90-day restriction | **Deleted, effective 2026-06-04** | A pattern day trader failing to meet a special maintenance margin call within five business days was permitted to trade "only on a cash available basis for 90 days or until the special maintenance margin call is met". This was *not* the consequence of merely holding less than $25,000. |
| FINRA Rule 4210(a)(17)–(19) — IML definitions | **In force from 2026-06-04** | Intraday margin level; IML-reducing transaction (one reducing what the customer could withdraw while still meeting the maintenance requirement, including withdrawals); intraday margin deficit (not less than the absolute value of the largest negative IML following an IML-reducing transaction that day). |
| FINRA Rule 4210(d)(2)(A)–(C) — determination and satisfaction | **In force from 2026-06-04** | Determine the deficit for each margin account (other than a good faith or portfolio margin account) on each day with an IML-reducing transaction; require it satisfied "as promptly as possible"; it remains outstanding until satisfied or until immediately after close of business on the **15th business day**. |
| FINRA Rule 4210(d)(2)(D) — 90 day freeze | **In force from 2026-06-04** | A customer who "makes a practice of failing to satisfy intraday margin deficits as promptly as possible" *and* misses the close of business on the **5th business day** is prevented from creating or increasing a short position or debit balance for **90 calendar days**, or until satisfied, "without regard to its expiration". Safe harbours: deficits not exceeding the lesser of **5% of equity or $1,000**, and deficits under extraordinary circumstances as determined by the member. |
| FINRA Rule 4210(d)(1) — house margin | In force, no expiry | Members formulate their own margin requirements and may institute higher requirements than the rule. This is the standing basis for any broker-specific day-trade policy after the phase-in. |
| FINRA Rule 4210(g)(1)(J)–(K) — portfolio margin | **In force from 2026-06-04** | Intraday risk monitoring in the written risk analysis methodology; accounts under $5 million equity must margin intraday risk substantially as they do end-of-day positions. |
| FINRA Rule 4210(b)(4) — minimum equity | In force | $2,000 general minimum. The `[$25,000 in the case of a "pattern day trader"]` text was struck by the same filing. |

**Implementation timing.** Effective date 2026-06-04, announced in Regulatory
Notice 26-10 (published 2026-04-20). Members needing more time may phase in
implementation over 18 months, to **2027-10-20**. The filing states FINRA
"believes members should be permitted for an interim period to continue to apply
the current day trading margin requirements where they deem appropriate — for
example, by account — while they prepare to implement the new provisions."
A count-based restriction encountered today may therefore be either a phase-in
holdover or a house requirement; both are the broker's, not FINRA's.

**NYSE Rule 431 is not a live citation.** It is a retired rule: NASD Rules 2520,
2521, 2522 and IM-2522 were consolidated and renumbered as FINRA Rule 4210, and
Incorporated NYSE Rule 431 and its interpretations were deleted from the
Transitional Rulebook effective 2010-12-02 (SR-FINRA-2010-024, Regulatory Notice
10-45). Cite FINRA Rule 4210.

## Broker surface

| Broker | Day-trade counter | Notes |
|---|---|---|
| Alpaca | **Removed** | `pattern_day_trader`, `daytrade_count`, `last_daytrade_count`, `daytrading_buying_power` and `last_daytrading_buying_power` were removed from the API by 2026-07-06; `buying_power` now carries intraday buying power. Alpaca adopted intraday margin on 2026-06-04 and states it no longer enforces the $25,000 minimum. |
| Other brokers | Verify per account | Whether a counter is still published, and whether a count-based policy still applies, is a per-broker and per-account question during the phase-in. Do not generalise from one broker's migration, and do not assume a broker that has migrated will not impose a house requirement under Rule 4210(d)(1). |

Unverified in this pass: the migration date and current house policy of brokers
other than Alpaca. Treat `DayTradePolicy.confirmed_with_broker` as `None` until
you have a dated answer from your own broker.

## Category

`regulatory-compliance-global` — see the top-level `mappings/` directory for how
this category rolls up across the full skill library.

## Sources

- FINRA Rule 4210 — <https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210>
- SR-FINRA-2025-017, rule text incl. deleted paragraphs (Exhibit 5) —
  <https://www.finra.org/sites/default/files/2025-12/SR-FINRA-2025-017.pdf>
- SEC approval, Release 34-105226, 2026-04-14 —
  <https://www.sec.gov/files/rules/sro/finra/2026/34-105226.pdf>
- FINRA Regulatory Notice 26-10 (effective date, 18-month phase-in) —
  <https://www.finra.org/rules-guidance/notices/26-10>
- FINRA investor guidance, "Understanding the New Intraday Margin Requirements" —
  <https://www.finra.org/investors/insights/intraday-margin-requirements>
- Interpretations of Rule 4210 valid from 2026-06-04 —
  <https://www.finra.org/rules-guidance/guidance/interps-4210-202606>
- Retired NYSE Rule 431 — <https://www.finra.org/rules-guidance/rulebooks/retired-rules/rule-431>
- Alpaca, "FINRA Retires the PDT Rule: Introducing Alpaca's New Intraday Margin
  Framework" —
  <https://alpaca.markets/blog/finra-retires-the-pdt-rule-introducing-alpacas-new-intraday-margin-framework/>
