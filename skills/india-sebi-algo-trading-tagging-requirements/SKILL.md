---
name: india-sebi-algo-trading-tagging-requirements
description: >-
  Use when a broker, empanelled algo provider or tech-savvy retail client sends
  algorithmic orders to NSE, BSE or MCX under the SEBI circular of 4 February 2025,
  covering algorithm tagging, registration and order-per-second thresholds. Direct
  market access flow is excluded.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: regulatory-compliance-global
  tags: sebi, algo-tagging, nse, bse, algo-id, otr-monitoring, pro-cli-category, ops-threshold
  brokers_frameworks: "SEBI Circular SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013 (4 Feb 2025); NSE/INVG/67858 Implementation Standards (5 May 2025); NSE/INVG/69255 Detailed Operational Modalities (22 Jul 2025); SEBI/HO/MRD1/DSAP/CIR/P/2020/107 OTR Guidelines; NSE NNF order structure; BSE Bolt Plus; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when building or reviewing the pre-trade layer that sits between an
algorithmic strategy and an Indian exchange session — as a **stock broker** offering algo
trading, an **empanelled algo provider** operating through a broker's vendor API, or a
**tech-savvy retail client** running your own algo over a broker's client API.

SEBI is the **Securities and Exchange Board of India**. The requirements sit in three
layers, and conflating them is the most common error in this area:

| Layer | Instrument | What it governs |
|---|---|---|
| Framework | **SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013**, 4 Feb 2025 | Who does what: brokers are the principal, algo providers their agents; every API algo order carries an Exchange-provided unique identifier; exchange permission per algo; static IP and API key controls; white box vs black box categorisation. **Prescribes no numbers.** |
| Implementation standards | **NSE/INVG/67858**, 5 May 2025 (issued under para 7(a); BSE and MCX issued matching standards) | The operative numbers: Threshold Order Per Second of 10, generic vs registered algo IDs, whose static IP, and the broker's duty to reject flow above the threshold. |
| Exchange order rules | **NSE/SURV/55281**, **NSE/CMTR/68802**, **NSE/MSD/67753** | Algo orders may not be market orders (pre-emptively rejected by the exchange); algo identity is the 13th digit of the 15-digit NNF field; IOC additionally barred in the commodity segment. |

The **Order-to-Trade Ratio** framework is older and separate again
(CIR/MRD/DP/09/2012 → SEBI/HO/MRD1/DSAP/CIR/P/2020/107 → the revision of 4 Feb 2026). It
is an economic disincentive levied on the **trading member**, per segment, per day — not a
per-order pre-trade reject.

## When NOT to Use

- **For Direct Market Access flow.** NSE/INVG/67858 Annexure J.1 is explicit: "These
  standards do not apply to trading under Direct Market Access (DMA), which will remain
  governed by the relevant provisions." The engine returns `OUT_OF_SCOPE_DMA` rather than
  an approval, because an approval here would be a false compliance record.
- **As the source of your thresholds.** 10 OPS is "initially set" by the exchanges and
  "may be adjusted ... after due notice to the market"; the OTR slab rates and boundaries
  are each exchange's own. Every number in this skill is a default you must re-confirm.
- **As the OTR figure the exchange will bill you.** The framework excludes orders within
  ±0.75% of the LTP, Designated Market Maker market-making orders, an equity-option
  premium band, and orders the exchange rejected outright. The engine nets off only the
  exemptions you pass it; with none passed it computes a conservative upper bound.
- **As the OPS throttle itself.** This is a gate that classifies one order against a rate
  you measured. Measuring orders per second against the broker server's calendar clock
  second, and shedding the excess, is the broker's own rate limiter.
- **Outside the Indian exchanges.** Nothing here transfers. See
  `mifid-ii-algo-trading-compliance-eu`, `sec-rule-15c3-5-risk-controls-us`,
  `mas-singapore-algo-trading-guidelines`, `hong-kong-sfc-algorithmic-trading-guidelines`.
- **As the system of record.** The report is a return value. Audit trail data for API
  orders must be available for at least 5 years (NSE/INVG/67858 Annexure I.a) — see
  `record-retention-periods-by-jurisdiction`.

## Prerequisites

- Python 3.10+ (`from __future__ import annotations`; standard library only).
- **Per order**: the Exchange-provided `algo_id`; `algo_tag_kind` (`REGISTERED` for an
  exchange-registered algo, `GENERIC` for the standardised sub-threshold tag);
  `order_source` (`CLIENT_API` / `VENDOR_API` / `BROKER_ALGO` / `IBT_STWT` / `DMA`);
  `exchange`; `segment`; `order_type`; `client_category` (`PRO`/`CLI`);
  `static_ip_whitelisted`; and optionally the 15-digit `nnf_id`.
- **Measured, not assumed**: `orders_per_second_ops` — the client's order flow to the
  broker, measured per exchange/segment on the broker server's calendar clock second
  (NSE/INVG/67858 Annexure B.2).
- **Per member, per segment, per day**: `total_order_messages` (submits + modifies +
  cancels), `total_executed_trades`, `exempt_order_messages`, and
  `prior_cooling_off_instances_30d` from your own durable records.

## Workflow

1. **Establish the channel first — it decides whether this framework applies at all.**
   - **Decision point — DMA is not "algo trading with extra steps".** If the flow is DMA,
     stop: Annexure J.1 carves it out. Record that determination; do not run it through
     this gate and file the result as compliance evidence.

2. **Exchange-provided algo ID on every order (SEBI para 5.II(b); Annexure G).** The rule
   is "All algo orders (Below and above the threshold)" — there is no untagged tier.
   - **Decision point — the ID comes from the Exchange, never from you.** A registered
     algo carries its own exchange algo ID; sub-threshold flow carries the generic tag the
     Exchange provides (Annexure B.3). A broker-invented string in that field is an
     untagged order that merely looks tagged.
   - **Decision point — the NNF 13th digit is what the exchange actually reads.** NSE
     identifies an algo by the 13th digit of the 15-digit NNF field and pre-emptively
     rejects on an NNF/Algo-ID mismatch. If you populate `nnf_id`, this gate checks that
     digit; if you leave it `None` it makes no claim about it, which is not the same as
     the check passing.

3. **Threshold Order Per Second — this is a registration trigger, not a speed limit.**
   Below 10 OPS per exchange/segment a client's algo needs no registration (Annexure B.2);
   to go faster the algo **must be registered with each Exchange** where it will be used
   (Annexure C.1).
   - **Decision point — the consequence of breaching it is rejection, not a warning.**
     Annexure B.5: "the broker shall reject/not accept/not process any orders exceeding
     the OPS limit". The gate blocks; it does not log and pass.
   - **Decision point — a registered algo is not gated by this threshold.** Section B is
     headed "Standards around APIs *without registering algo*". Applying the 10 OPS reject
     to registered flow would block exactly the algos that registered in order to exceed it.
   - **Decision point — exactly 10 OPS is genuinely ambiguous in the source.** Annexure
     B.2/F say "not exceeding 10" and that flow "below" the threshold needs no
     registration; those readings differ at the boundary. The gate rejects strictly above
     and raises `OPS_EXACTLY_AT_THRESHOLD_BOUNDARY` at the boundary. Resolve it with your
     exchange rather than letting a default decide.

4. **Order type (NSE/SURV/55281; NSE/CMTR/68802; NSE/MSD/67753 §8.1.2.1).** Algo market
   orders are prohibited and the exchange rejects them pre-emptively. IOC is additionally
   barred for algos in the commodity segment.
   - **Decision point — reaching the exchange's own rejection is already a failure.** The
     pre-emptive cancel is a backstop, not your control; catching it in your own gate is
     what keeps the order off the wire.

5. **Access controls (SEBI para 5.I(d); Annexure A.1, A.5).** Static IP whitelisting,
   unique vendor-client-specific API keys, OAuth-only authentication, two-factor
   authentication, and empanelled algo providers only.
   - **Decision point — whose IP depends on the channel.** Client-generated algos use the
     client's IP; provider algos the vendor's or the client's; broker algos the broker's or
     the client's. Per the NSE retail-algo FAQ of 3 Nov 2025 (Q3, Q6) a *client* static IP
     is required only for a tech-savvy investor using an API — a member front-end order
     does not carry that gate, and blocking it would be a control you invented.

6. **PRO/CLI account tagging.** Verify the order carries `PRO` or `CLI` correctly.
   - **Decision point — this is not an algo-tagging requirement.** It is a separate,
     long-standing exchange order attribute segregating a member's proprietary account
     from client accounts. Do not cite the algo circulars for it.

7. **Classify the member's daily OTR — and get the cooling-off rule right.**
   - **Decision point — one day at 2,000 is a charge, not a suspension.** The suspension
     bites "on the third instance of OTR being 2000 or more, in last 30 days (rolling
     basis)" (SEBI/HO/MRD1/DSAP/CIR/P/2020/107). The gate reports
     `OTR_COOLING_OFF_LEVEL_REACHED` for instances 1 and 2 and
     `OTR_COOLING_OFF_TRIGGERED` only on the third.
   - **Decision point — no trades means the ratio is undefined, not low.** The engine
     returns `None` and `OTR_UNDEFINED_NO_TRADES`. A day of order messages with no fill is
     the worst case, and it is separately assessed under the exchange's low-trade-count and
     quote-stuffing penalties.
   - **Decision point — an OTR breach does not block the order.** It is levied on the
     member. Blocking on it would invent a pre-trade control SEBI did not impose.

8. **Record every decision.** Approvals as well as rejections, with every violation raised
   — not just the headline — and with the OTR populated on blocked orders too. Persist to a
   durable append-only store; API order and trade audit data must be available for at least
   5 years (Annexure I.a).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Expanding SEBI as "Securities and Futures Board of India".** It is the **Securities and
  Exchange Board of India**. A wrong regulator name in a compliance artefact is the kind of
  error an inspection finds first.
- **Citing SEBI for the 10 OPS number.** The February 2025 circular does not contain it —
  footnote 2 defers the threshold to the Broker's Industry Standards Forum under the aegis
  of the exchanges. The number lives in NSE/INVG/67858 Annexure B.2/F, is "initially set",
  and is adjustable after notice to the market.
- **Treating 10 OPS as a rate limit for everyone.** It is the line above which an algo must
  be *registered*. Applying it to registered algos blocks precisely the flow that
  registration exists to permit.
- **Logging an OPS breach and letting the order through.** Annexure B.5 makes rejection a
  duty, not an option.
- **Assuming sub-threshold flow needs no tag.** Annexure G covers algo orders "Below and
  above the threshold". The generic Exchange tag is still an Exchange tag.
- **Reporting a zero-trade day as a low OTR.** Dividing by zero "safely" by returning the
  message count turns 400 messages and no fills into "an OTR of 400" — below the 500 slab
  floor. The worst day of the month reads as the cleanest.
- **Computing OTR from raw totals.** Orders within ±0.75% of the LTP, DMM market-making
  orders, the equity-option premium band and exchange-rejected orders do not count. A raw
  ratio overstates the number and can manufacture a cooling-off instance that never
  happened.
- **Rounding before the threshold test.** A true OTR of 1999.999 rounds to "2000.00"; if
  the comparison runs on the rounded figure it manufactures an instance towards a
  cooling-off suspension that never occurred. Round for display, never before the test.
- **Treating a single 2,000 day as a suspension.** Two more are needed inside a rolling
  30-day window. Halting trading on the first one is a self-inflicted outage.
- **Attaching the OTR to an order.** It is a trading-member, per-segment, daily figure. A
  report that reads as "this order's OTR" invites someone to reset it per strategy.
- **Running DMA flow through the retail-algo gate.** Annexure J.1 carves DMA out entirely;
  an approval from this gate is not DMA compliance.
- **Sending an algo market order and relying on the exchange to catch it.** The pre-emptive
  rejection is a backstop, and in the capital market segment it does not apply to the
  closing session or post-close.
- **Recording only the first breach.** An untagged order at 50 OPS with a bad category is
  not "an untagged order"; filing it as one loses the rest.
- **Filing a blocked order with zeroed metrics.** If the reject path reports OTR as 0.0,
  the audit log no longer says what was actually stopped.
- **Forgetting black box algos carry an extra obligation.** Under SEBI para 5.V the algo
  provider must register as a Research Analyst and maintain a research report per algo, and
  a change in logic means registering it as a fresh algo.

## Verification

- Instantiate `SebiAlgoTaggingEngine()` and confirm the defaults it ships with:
  `threshold_ops == 10.0`, `otr_penalty_slab_floor == 500.0`,
  `otr_cooling_off_level == 2000.0`, `cooling_off_instance_count == 3`,
  `cooling_off_lookback_days == 30`.
- Compliant registered order (`NSE_ALGO_99812`, `REGISTERED`, `PRO`, `CLIENT_API`, `LIMIT`,
  static IP whitelisted) with 200 messages / 5 trades: expect `SEBI_TAGGING_APPROVED`,
  `calculated_otr_ratio == 40.0`, `otr_status == "OTR_NORMAL"`, `violations == ()`.
- `algo_id=""` and `algo_id="   "`: expect `REJECTED_UNTAGGED_ALGO` — and, with a valid
  `PRO` category, `is_category_valid` still `True` (the reject path must not file a false
  statement about a field it did not fail).
- `is_registered_with_exchange=False` with `algo_tag_kind="REGISTERED"`: expect
  `REJECTED_UNREGISTERED_ALGO`; with `algo_tag_kind="GENERIC"`: expect approval.
- `GENERIC` tag at 10.5 OPS: `REJECTED_OPS_THRESHOLD_BREACH`. At 9.99: approved. At exactly
  10.0: approved but with `OPS_EXACTLY_AT_THRESHOLD_BOUNDARY` in `violations`. A
  `REGISTERED` algo at 250 OPS: approved.
- `order_type="MARKET"`: `REJECTED_ALGO_MARKET_ORDER`. `order_type="IOC"` in `COM`:
  `REJECTED_RESTRICTED_ORDER_TYPE`; the same IOC order in `FO`: approved.
- `static_ip_whitelisted=False` from `CLIENT_API`, `VENDOR_API` and `BROKER_ALGO`:
  `REJECTED_STATIC_IP_NOT_WHITELISTED`; from `IBT_STWT`: approved.
- `order_source="DMA"`: expect `OUT_OF_SCOPE_DMA` with `blocks_order` false — and the same
  status even when the order is untagged, so the carve-out never reads as an approval. The
  OTR is still reported (that framework has no DMA carve-out), so 200/5 must read `40.0`
  and `OTR_NORMAL`, never `None`/`OTR_UNDEFINED_NO_TRADES`.
- `nnf_id` of 15 digits with a 13th digit of `4`: approved; with `1`: `REJECTED_NNF_TAG_NOT_ALGO`;
  a 5-digit or non-numeric value: rejected with `NNF_ID_MALFORMED`; `None`: no claim made.
- OTR boundaries, from independently derived counts: 2495/5 = 499.0 → `OTR_NORMAL`;
  2500/5 = 500.0 → `OTR_PENALTY_SLAB`; 19,990/10 = 1,999.0 → `OTR_PENALTY_SLAB`;
  20,000/10 = 2,000.0 → `OTR_COOLING_OFF_LEVEL_REACHED`. None of these block the order.
- 25,000/10 = 2,500 with `prior_cooling_off_instances_30d` of 0, 1 and 2: expect
  `OTR_COOLING_OFF_LEVEL_REACHED`, `OTR_COOLING_OFF_LEVEL_REACHED`, then
  `OTR_COOLING_OFF_TRIGGERED` with `otr_cooling_off_instances_30d == 3`.
- 400 messages / 0 trades: `calculated_otr_ratio is None`, `OTR_UNDEFINED_NO_TRADES`, and
  `OTR_UNDEFINED_NO_TRADES` in `violations` — never `400.0`.
- 1000 messages of which 400 exempt / 4 trades: `calculated_otr_ratio == 150.0`.
- Rounding boundaries, which must be judged on the unrounded ratio: 3,999,998/2,000 =
  1999.999 reports `2000.0` but must classify `OTR_PENALTY_SLAB` with the instance count
  unchanged; 999,998/2,000 = 499.999 reports `500.0` but must classify `OTR_NORMAL`;
  4,000,002/2,000 = 2000.001 reports `2000.0` and must still classify
  `OTR_COOLING_OFF_LEVEL_REACHED`.
- An order that is untagged **and** miscategorised **and** a market order **and** has no
  whitelisted IP **and** sits on a third cooling-off instance: headline
  `REJECTED_UNTAGGED_ALGO`, with all five violations present in `violations`.
- Malformed payloads raise `ValueError`, not a rejection: unknown exchange/segment/source/
  order type/tag kind/side, non-positive quantity, NaN or negative price, blank symbol,
  negative OPS. `SebiOtrMetrics` rejects negative counters and
  `exempt_order_messages > total_order_messages`.
- Run `python -m unittest discover -s skills/india-sebi-algo-trading-tagging-requirements/scripts`
  (62 tests) and confirm a 100% pass rate.

## Related Skills

- `order-to-trade-ratio-fee-penalty-avoidance`
- `algo-trading-disclosure-to-exchange-membership`
- `execution-algorithm-kill-switch-integration`
- `wash-trade-and-spoofing-self-detection`
- `record-retention-periods-by-jurisdiction`
- `regional-broker-data-residency-constraints`
- `zerodha-kite-postback-webhook-verification`
- `upstox-oauth-refresh-token-rotation`
- `headless-broker-auth-patterns`
- `mas-singapore-algo-trading-guidelines`
- `hong-kong-sfc-algorithmic-trading-guidelines`
- `mifid-ii-algo-trading-compliance-eu`
- `sec-rule-15c3-5-risk-controls-us`
- `finra-algo-trading-registration-requirements`
