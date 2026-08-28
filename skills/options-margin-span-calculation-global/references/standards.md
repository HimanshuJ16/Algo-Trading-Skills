# Broker & Framework Coverage — options-margin-span-calculation-global

| Margin Standard / Venue | Relevance to this skill | Source |
|---|---|---|
| CME SPAN (legacy scenario-based) | The methodology this skill approximates. Risk arrays hold 16 scenarios: scenarios 1–14 pair volatility up/down against price moves of 0, ±1/3, ±2/3 and ±1 of the price scan range; scenarios 15–16 move price by a multiple of the scan range ("3 times the Price Scan Range") and cover only a fraction of the resulting loss. The risk requirement is floored at the short option minimum, and total SPAN margin is that requirement less the net option value. | [CME risk-array specification](https://cmegroupclientsite.atlassian.net/wiki/spaces/pubsub/pages/457411520/Risk+Arrays+-+Standard); [CME SPAN methodology](https://www.cmegroup.com/clearing/files/span-methodology.pdf) |
| CME SPAN 2 | **Not reproducible by this skill.** A filtered-historical-simulation VaR framework replacing legacy SPAN at CME on a phased product rollout that began with energy and equity products. Confirm which framework a CME product is on before applying a scenario scan. | [CME SPAN 2 rollout](https://www.cmegroup.com/solutions/risk-management/performance-bonds-margins/span-methodology-overview/launching-span-2.html) |
| OCC TIMS / FINRA Rule 4210(g) portfolio margin (US) | **A different model, not tuned SPAN.** Customer portfolio margin stresses each product group over an *asymmetric* range: −8%/+6% for high-capitalisation broad-based indexes, ±10% for non-high-capitalisation broad-based indexes, ±15% for sector indexes and individual equities. Use these ranges when setting a scan range for a US portfolio-margin account, and treat the output as indicative. | [FINRA Rule 4210](https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210); [OCC Customer Portfolio Margin](https://www.theocc.com/Risk-Management/Customer-Portfolio-Margin) |
| NSE Clearing SPAN + Extreme Loss Margin (India) | Initial margin is SPAN plus a flat Extreme Loss Margin on notional. Published base ELM rates are 2% for index futures and 3.5% for stock futures, with product- and event-specific overlays (for example an additional ELM on short index option contracts on expiry day, effective 20 November 2024). The `exposure_margin_pct` parameter models this overlay; it has no CME analogue. | [NSE Clearing — margins](https://www.nseclearing.in/risk-management/equity-derivatives/margins) |
| US Reg-T strategy-based margin (FINRA Rule 4210(f)(2), Cboe Rule 10.3) | Out of scope here — a template-based rule set producing materially different numbers. See the sibling skill `multi-leg-strategy-margin-optimization`. | — |

## Category

`multi-asset-derivatives` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Parameter Provenance

Every scan parameter — price scan range, volatility scan range, short option minimum,
extreme-move multiplier and its cover fraction — is a daily exchange-published input, not a
modelling choice. The values carried as defaults in `scripts/span_approx.py` are placeholders
retained for backward compatibility and do not correspond to any particular contract. The
extreme-move cover fraction in particular is an exchange parameter whose published value has
varied across CME documentation revisions; read it from the parameter file rather than relying
on the default.

## Regulatory & Operational Notes

- Intersects with exchange initial and maintenance margin rules and with broker margin-call
  and liquidation protocols. A margin figure that is understated is the dangerous direction of
  error: it permits a position the account cannot carry to the next recalculation.
- In India, SEBI's peak margin regime measures margin obligation from intraday snapshots rather
  than end-of-day positions, so a position that is compliant at the close can still breach.
- Brokers routinely impose house requirements above the exchange or SRO minimum. Reconcile
  against the broker's own calculator before redeploying capital an estimate says is free.
