---
name: pattern-day-trader-rule-compliance-us
description: >-
  Use when a bot trades US equities/options in a margin account under $25,000 in equity, to avoid triggering FINRA's Pattern Day Trader restriction, which can freeze the account's day-trading ability
domain: algorithmic-trading
subdomain: regulatory-compliance-global
tags: ["regulatory-compliance-global", "finra-rule-4210-(pattern-day-trader)"]
brokers_frameworks: ["FINRA Rule 4210 (Pattern Day Trader)"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this for any bot trading US equities or equity options in a margin account, specifically when account equity is below the $25,000 threshold FINRA sets for unrestricted day trading. FINRA's Pattern Day Trader (PDT) rule flags an account as a "pattern day trader" if it executes 4 or more day trades (opening and closing the same position same-day) within any rolling 5-business-day window, while day trades represent more than 6% of total trades in that window. Once flagged, an account under $25,000 equity is restricted from further day trading for 90 days (or until equity is brought above the threshold) — a bot that isn't tracking this can trip the restriction mid-strategy and have its ability to exit same-day positions abruptly limited, which is a materially different failure mode than a simple rejected order.

## Prerequisites

- Accurate, rolling tracking of day-trade count over the trailing 5 business days (not calendar days — exchange holidays and weekends must be excluded consistently with `global-exchange-holiday-calendar-handling`)
- Current account equity value, reconciled against the broker's own equity calculation (not just the bot's internal position tracking), since the $25,000 threshold is evaluated by the broker against actual account equity

## Workflow

1. Maintain an explicit rolling log of day trades (same-symbol open-then-close within the same trading day) over the trailing 5 business days, independent of and reconciled against whatever count the broker itself reports (some brokers expose a day-trade counter via their API; treat this as the authoritative figure and use it to validate the bot's own tracking, not as a replacement for tracking it locally).
2. Before allowing a new day trade that would push the rolling count to the 4th within the window, check current account equity against the $25,000 threshold; if under threshold, either block the day trade or route it through explicit human confirmation depending on the strategy's risk posture — do not let the strategy logic place the 4th day trade unknowingly and discover the restriction only when the broker rejects a subsequent order or freezes the account.
3. Distinguish a day trade from an overnight-held position explicitly in the bot's own trade-classification logic — a position opened one day and closed the next (even if only briefly held past market close) is not a day trade, and misclassifying this either overstates the day-trade count unnecessarily (limiting legitimate trading) or, more dangerously, underclassifies actual day trades and lets the bot exceed the threshold without noticing.
4. If a strategy fundamentally requires more than 3 day trades per rolling 5-day window on an account under $25,000, this is a structural constraint the strategy design must account for (e.g., trading in a cash account instead of margin, which has different but distinct settlement-related constraints, or ensuring the account is funded above the PDT threshold) rather than something the bot's code can work around — do not build logic that attempts to disguise day trades or route around the rule, which is a compliance violation, not just an engineering workaround.
5. Log every day-trade-count check and any block/restriction decision with enough detail (rolling count at time of check, account equity at time of check) to reconstruct after the fact why a particular order was blocked or allowed — this matters both for debugging and for demonstrating the bot's compliance logic was actually functioning if ever reviewed.
6. Re-verify current PDT threshold and rule details periodically rather than treating the $25,000 figure and 5-day/4-trade/6% parameters as permanently fixed — while these have been stable for a long period, regulatory parameters can change, and hardcoding them without a periodic review step risks silently operating against outdated rules.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Tracking day trades only in the bot's own internal model without reconciling against the broker's authoritative day-trade counter, risking drift between the two.
- Misclassifying a trade held briefly past market close as a day trade (or vice versa), skewing the rolling count.
- Discovering the PDT restriction only when the broker rejects an order or freezes day-trading ability, rather than proactively blocking the 4th day trade before it's placed on an under-threshold account.
- Attempting to design strategy logic that circumvents or disguises day-trade classification — this is a compliance issue, not an engineering problem to solve around.
- Hardcoding the $25,000/4-trade/5-day/6% parameters without a periodic check that these remain the current rule.

## Verification

- Construct a test scenario (in a paper/sandbox account, or via careful historical trade-log analysis) with exactly 4 day trades within a rolling 5-business-day window on a simulated sub-$25,000 account, and confirm the bot's logic blocks or flags the 4th before submission.
- Confirm the bot's internally tracked day-trade count matches the broker's own reported day-trade counter over a live/paper trading period.
- Confirm log records exist showing the rolling count and account equity at the time of every PDT-relevant blocking decision, sufficient to reconstruct the decision after the fact.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `paper-to-live-promotion-checklist`
