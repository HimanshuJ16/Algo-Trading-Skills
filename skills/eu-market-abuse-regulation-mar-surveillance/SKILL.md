---
name: eu-market-abuse-regulation-mar-surveillance
description: Trade-surveillance engine that screens order and trade batches for EU
  Market Abuse Regulation (Regulation (EU) No 596/2014) manipulative patterns —
  wash trading, spoofing/layering, quote stuffing — and assembles a draft STOR
  record for human analysis under Delegated Regulation (EU) 2016/957.
domain: Trade Surveillance & Regulatory Compliance
subdomain: Market Abuse & STOR Reporting (EU MAR)
tags:
- eu-mar
- market-abuse
- trade-surveillance
- stor-report
- spoofing-detection
- wash-trading
- quote-stuffing
brokers_frameworks:
- Regulation (EU) No 596/2014 (MAR)
- Commission Delegated Regulation (EU) 2016/957 (STOR RTS)
- Commission Delegated Regulation (EU) 2016/522 (manipulation indicators)
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when building or reviewing market-abuse surveillance for **EU/EEA** trading, where **MAR Article 16(1)-(2)** obliges market operators and investment firms operating a trading venue, and persons professionally arranging or executing transactions (PPAETs), to maintain effective arrangements to prevent and detect market abuse and to notify the National Competent Authority (NCA) *without delay* once reasonable suspicion is formed.

It fits post-trade / near-real-time batch screening of an order and trade log for three of the indicators in **Commission Delegated Regulation (EU) 2016/522, Annex II** and **MAR Annex I**: transactions with no change in beneficial ownership, layering/spoofing, and quote stuffing.

## When NOT to Use

- **Not for the UK.** Since 1 January 2021 the UK applies its own assimilated MAR and assimilated Delegated Regulation (EU) 2016/957, supervised by the FCA. The FCA is *not* an NCA under EU MAR; a UK STOR goes to the FCA under UK MAR, through FCA channels.
- **Not for crypto-assets.** MAR covers financial instruments admitted to trading (or for which admission has been requested) on an EU regulated market, MTF or OTF. The analogous crypto obligation is **Regulation (EU) 2023/1114 (MiCA) Article 92**.
- **Not as a filing client.** There is no EU-wide STOR endpoint. Each NCA prescribes its own channel with prior enrolment — BaFin via the MVP portal's STOR procedure, the AMF via the ROSA extranet. This skill produces a draft record only.
- **Not as a suspicion determination.** Delegated Regulation (EU) 2016/957 requires an appropriate level of *human analysis*. An alert is an input to that analysis, never a filing decision.
- **Not a real-time gate.** It scores finite batches after the fact. For pre-trade self-match prevention see `wash-trade-and-spoofing-self-detection`.

## Prerequisites

- Order and trade event log with `event_id`, `cl_ord_id`, `isin`, `symbol`, `side`, `order_qty`, `price`, `event_type` (`NEW`/`MODIFY`/`CANCEL`/`FILL`/`REJECT`), `timestamp_ns`, `buyer_account_id`, `seller_account_id`, and — where known — `account_id` for the order's owning account.
- `timestamp_ns` in nanoseconds since the Unix epoch, UTC. Sub-100ms lifespan logic is only meaningful on synchronised business clocks (**Commission Delegated Regulation (EU) 2017/574, RTS 25** for MiFID II venues and their members).
- Batches that contain the **whole order lifecycle**: a `CANCEL` whose `NEW` fell outside the batch cannot be timed and is excluded from the ratio (reported as `unmatched_cancels`).
- An account → beneficial-owner mapping, if sub-accounts are used. Without it, self-execution across two account ids of one owner is invisible.
- Calibrated detection parameters. The defaults (`0.90` cancel ratio, `100ms` lifespan, `500 msgs/sec`) are **library heuristics, not regulatory thresholds** — MAR prescribes no numbers.

## Workflow

1. **Validate the batch before scoring it.** `audit_events_for_mar_patterns` rejects unknown event types or sides, non-positive quantities, non-finite or negative prices, negative timestamps, events identifying no instrument, and duplicate `event_id`s. A duplicated event would inflate both the cancel ratio and the message rate, so it is an error, not a warning. Events may arrive unsorted — they are ordered internally, so the result never depends on input ordering.
2. **Resolve beneficial ownership first.** Every detector groups by *beneficial owner*, not by raw account id. Pass `beneficial_owner_map` so sub-accounts collapse onto the owning entity.
3. **Wash trading — test ownership, not string equality.** A `FILL` whose buyer and seller resolve to the same beneficial owner is flagged `WASH_TRADE_ALERT` (`CRITICAL`), mapping to the MAR Annex I Section A indicator "no change in beneficial ownership".
4. **Spoofing/layering — ratio *and* lifespan *and* side.** Per (owner, instrument): join `NEW` to `CANCEL` on `cl_ord_id`, keep cancels whose lifespan is at or below `spoof_max_lifespan_ms`, and compare `fast_cancels / new_orders` against the threshold once at least `min_orders_for_cancel_ratio` orders exist. If an opposite-side `FILL` lands inside the pattern window the alert is `HIGH` and `opposite_side_fill_observed=True` — that is the Annex II layering/spoofing shape. Without it the alert is `MEDIUM`: a non-bona-fide-order indicator, not confirmed spoofing. Set `require_opposite_side_fill=True` to suppress the weaker case entirely.
5. **Quote stuffing — sliding window, not calendar buckets.** Peak rate is the largest count of `NEW`/`MODIFY`/`CANCEL` messages inside *any* one-second window per (owner, instrument). Fills are excluded — an execution is not a quote. Fixed second-buckets would split a burst that straddles a boundary and miss it.
6. **Assemble the STOR draft, then hand it to a human.** `stor_filing_payload` carries the legal basis, the template source (Delegated Regulation (EU) 2016/957, Annex), the instruments, the beneficial owners, the timestamp window, the exact detection parameters used, `status = DRAFT_PENDING_HUMAN_REVIEW`, and the retention obligation. Transposing it into the NCA's template and forming reasonable suspicion is the compliance officer's job.
7. **Retain the analysis for five years.** Delegated Regulation (EU) 2016/957 requires the analysis of examined orders and transactions to be kept for five years — *including cases where no STOR was submitted, and the reasons why*. Persist `detection_parameters` alongside the alerts so a decision can be reconstructed years later.

> Full procedure: see `references/workflows.md`.
> Standards and calibration guidance: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating an alert as a STOR.** Delegated Regulation (EU) 2016/957 requires human analysis. Auto-filing every alert both floods the NCA and skips the judgement the RTS asks for; the engine therefore never emits a "ready to submit" state.
- **Batching STORs to file them together.** MAR Article 16 requires notification *without delay* once reasonable suspicion is formed. Holding a report back to accumulate cases is expressly contrary to that.
- **Computing a cancel ratio across the whole batch.** One spoofer inside honest flow is diluted to nothing (9 fast cancels out of 50 batch-wide orders looks like 18%, while that participant's own ratio is 90%). Group by beneficial owner and instrument, always.
- **Using the cancel ratio without the order lifespan.** A liquidity provider that quotes continuously and re-prices every few hundred milliseconds can exceed a 90% cancel ratio legitimately. What distinguishes layering is *how briefly* the orders rested, and whether the other side traded.
- **String-matching account ids for wash trades.** MAR Annex I Section A asks whether there was a change in *beneficial ownership*. Two sub-accounts of one entity self-crossing is a wash trade whose account ids differ; conversely, a broker's internal give-up representation can put one account on both sides of a non-abusive booking — check the mapping before escalating.
- **Setting the quote-stuffing threshold below normal market-making rates.** A designated market maker meeting quoting obligations in a liquid name will breach a naive rate limit continuously. Calibrate per venue and instrument liquidity tier and record the rationale.
- **Assuming timestamps are comparable.** Fast-cancel logic at 100ms resolution is meaningless across desynchronised clocks; on MiFID II venues the applicable standard is RTS 25 (Delegated Regulation (EU) 2017/574).
- **Citing the wrong RTS.** The STOR template lives in the Annex to **Delegated Regulation (EU) 2016/957**. RTS 25 is MiFID II business-clock accuracy — relevant to timestamps, not to STOR content.

## Verification

- Instantiate `EuMarSurveillanceEngine`. Submit a `FILL` whose buyer and seller resolve to the same beneficial owner — expect one `WASH_TRADE_ALERT` at `CRITICAL` and a `stor_filing_payload` whose `status` is `DRAFT_PENDING_HUMAN_REVIEW`.
- Submit 10 `NEW` orders with 9 cancelled after 5ms — expect one `SPOOFING_ALERT` at `MEDIUM`, attributed to the owning entity and the traded instrument. Re-run with the same cancels at 500ms — expect **no** alert. Add an opposite-side `FILL` inside the window — expect `HIGH` with `opposite_side_fill_observed=True`.
- Submit 150 order-book messages 1ms apart against `quote_rate_threshold_per_sec=100` — expect one `QUOTE_STUFFING_ALERT`; the same 150 messages 100ms apart must not alert.
- Run `python -m unittest discover -s skills/eu-market-abuse-regulation-mar-surveillance/scripts`.

## Related Skills

- `wash-trade-and-spoofing-self-detection`
- `mifid-ii-algo-trading-compliance-eu`
- `order-to-trade-ratio-fee-penalty-avoidance`
- `cross-venue-latency-arbitrage-defensive-design`
