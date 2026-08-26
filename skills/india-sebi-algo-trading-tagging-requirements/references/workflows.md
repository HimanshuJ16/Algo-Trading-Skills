# Workflows — india-sebi-algo-trading-tagging-requirements

The deep procedure behind `SKILL.md`. Paragraph references are to
SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013 (4 Feb 2025) and to the Annexure of
NSE/INVG/67858 (5 May 2025); full citations in `references/standards.md`.

---

## 0. Establish the channel before anything else

The channel decides whether the framework applies, whose static IP is required, and which
tag the order should carry.

| `order_source` | Who is running the algo | Static IP required from | In scope? |
|---|---|---|---|
| `CLIENT_API` | Tech-savvy client's own algo over the broker's client API | The client (Annexure A.5) | Yes |
| `VENDOR_API` | Empanelled algo provider over a vendor API key | The vendor or the client (A.5) | Yes |
| `BROKER_ALGO` | Broker-generated algo (Annexure D) | The broker or the client (A.5) | Yes |
| `IBT_STWT` | Internet / wireless member front-end | Not from the client (NSE FAQ Q3, Q6) | Yes |
| `DMA` | Direct Market Access | — | **No — Annexure J.1** |

**Stop on DMA.** Annexure J.1: "These standards do not apply to trading under Direct
Market Access (DMA), which will remain governed by the relevant provisions." The engine
returns `OUT_OF_SCOPE_DMA` and makes no determination. Filing an approval from this gate as
DMA compliance evidence is worse than filing nothing: it asserts a check that was never run.

---

## 1. Algo ID tagging audit

**The rule.** SEBI para 5.II(b) and Annexure G: every algo order carries a unique
identifier **provided by the Exchange**, to establish an audit trail. Annexure G is
explicit that this covers orders "Below and above the threshold" — there is no untagged
tier for slow flow.

**The two tag kinds.**

- `REGISTERED` — an algo registered with the exchange, carrying the algorithm ID the
  exchange issued on registration (Annexure C.2 for client algos, D.1 for broker algos,
  E.2 for provider algos). Provider algo IDs "may be used across members once registered".
- `GENERIC` — the standardised tag for sub-threshold flow that needs no registration.
  Annexure B.3: "a generic algo ID shall be provided by the Exchange for such Algos". Per
  NSE FAQ Q7, for retail flow through a client direct API or member front-end the tagging
  is first 12 digits `444444444444` and a 13th digit of `0`, `2` or `4`.

**Procedure.**

1. Reject a blank or whitespace-only `algo_id` as `REJECTED_UNTAGGED_ALGO`. It does not
   matter how the order was generated; an algo order with no exchange identifier cannot be
   traced, which is the entire point of para 5.II(b).
2. If `algo_tag_kind == REGISTERED`, confirm `is_registered_with_exchange`. A `REGISTERED`
   tag on an algo the firm cannot evidence a registration for is a misstatement to the
   exchange, not a formatting issue → `REJECTED_UNREGISTERED_ALGO`.
3. If you populate `nnf_id`, the engine checks the **13th digit of the 15-digit NNF field**
   — the field NSE actually reads to decide whether an order is an algo order
   (NSE/CMTR/68802). A malformed NNF (wrong length, non-numeric) and a non-algo 13th digit
   are both rejections; leaving `nnf_id` as `None` means the check did not run, which is
   not the same as it passing.

**Change control.** Para 5.II(b) also requires the broker to "seek approval from the
Exchange for any modification or change to the approved algos", and Annexure D.3 says the
same for broker algos. A code change to a registered strategy is a regulatory event, not
just a deploy. Wire it to your release process — see
`strategy-research-to-production-pipeline-governance`.

---

## 2. Threshold Order Per Second (TOPS)

**What the threshold does.** It is the line above which an algo must be **registered**, not
a universal speed limit. Annexure B.2/F: "initially set at not exceeding 10 orders per
second per exchange/segment and may be adjusted by the stock exchanges as needed after due
notice to the market."

**How it is measured.** Per exchange and per segment, on the flow "from the client to the
broker via API", "basis the calendar clock second of the broker server" (Annexure B.2).
Measuring it on the client side, aggregated across venues, or on a sliding window rather
than the clock second, all give a different number from the one the broker is judged on.

**Consequences, by tag kind.**

- `GENERIC` above the threshold → **reject**. Annexure B.5: "the broker shall reject/not
  accept/not process any orders exceeding the OPS limit, in accordance with their policy."
  The remedy is registration (Annexure C.1), not a louder log line.
- `REGISTERED` → the registration threshold does not gate it. Section B is headed
  "Standards around APIs *without registering algo*"; applying its reject to registered
  flow blocks exactly the algos that registered in order to exceed it.

**The boundary at exactly 10.** Annexure B.2 and F say "not exceeding 10 orders per
second"; B.2 also says flow "below the defined Threshold Order Per Second" needs no
registration. Those two wordings do not agree at exactly 10 OPS, and the source does not
resolve it. Since the rejection duty in B.5 is worded "exceed"/"exceeding", the engine
rejects strictly above and raises `OPS_EXACTLY_AT_THRESHOLD_BOUNDARY` at the boundary so
the ambiguity appears in the audit record. **Get your exchange's reading in writing** and
set your own client limit below the boundary; Annexure F expressly lets a broker set a
lower per-client threshold.

**API key separation.** Annexure A.4: where a client holds multiple API keys, "non-registered
algos are run only through one of the predefined API keys. Other API keys can be used only
for registered algos." A single key carrying both makes the tag kind unverifiable at the
broker.

---

## 3. Order type restrictions

- **Algo market orders are prohibited.** NSE/SURV/55281 reiterated the Market Price
  Protection check, and NSE/CMTR/68802 extended pre-emptive exchange rejection of algo
  market orders to the capital market segment (from 7 Jul 2025), identifying the algo by
  the 13th NNF digit. **Not applicable to the closing session / post-close in the capital
  market segment**, so the exchange backstop has a gap your own control should not.
- **IOC additionally barred in the commodity segment.** NSE/MSD/67753 §8.1.2.1: "Immediate
  Or Cancel (IOC) and Market orders shall not be allowed to be placed using algorithmic
  trading" in the Commodity segment.
- **Exchanges may add more.** Annexure B.4 lets exchanges specify further restricted order
  types, contracts and securities for client algos, and requires that broker APIs not
  permit them. Treat the engine's list as today's floor and re-check it against the venue.

Reaching the exchange's own pre-emptive rejection is already a control failure: the order
left your system.

---

## 4. Access controls

SEBI para 5.I(d) and Annexure A / I:

- No open APIs. Access "only through a unique vendor client specific API key and static IP
  whitelisted by the broker".
- **OAuth-based authentication only** — "all other authentication mechanisms shall be
  discontinued" — plus two-factor authentication on API access.
- Static IP per §0 above. A client may register a secondary IP for redundancy (A.2) and may
  change a mapped IP "not more than once a calendar week" (A.6). An IP maps to one client,
  shareable only within a family as defined in SEBI/HO/MIRSD/MIRSD-PoD1/P/CIR/2024/169 (A.7).
- **All API sessions logged out daily** before the next trading day starts (A.8).
- Empanelled algo providers only, with broker due diligence before onboarding
  (para 5.I(d), 5.III(c)).

See `headless-broker-auth-patterns` and `upstox-oauth-refresh-token-rotation` for the token
mechanics, and `regional-broker-data-residency-constraints` for where the data may sit.

---

## 5. PRO/CLI account category

`PRO` (the member's proprietary account) versus `CLI` (a client account) is a long-standing
exchange order attribute enforcing the segregation of proprietary and client trading. It is
**not** imposed by the algo circulars, and this skill checks it only because an algo order
still has to carry it correctly. Do not cite SEBI's algo framework as authority for it, and
do not let an algo-tagging review substitute for the separate pro-account controls.

---

## 6. Order-to-Trade Ratio surveillance

**Scope.** Trading member level, per segment, per trading day, over algo orders and algo
trades. Not per order, not per algo, not per strategy.

**What counts.** Order messages means submissions plus modifications plus cancellations.
What does **not** count:

- Orders within **±0.75% of the LTP** (SEBI/HO/MRD/DP/CIR/P/2018/62 para 14).
- Since **6 April 2026**: algo orders by **Designated Market Makers** for market making,
  and an equity-option premium band (SEBI circular of 4 Feb 2026).
- Orders the exchange rejected outright (e.g. outside the price band) — per the NSE
  Consolidated Penalty FAQ these are not counted in order messages.

Pass these as `exempt_order_messages`. The engine will not guess them: with none passed it
computes a deliberately conservative upper bound, which is safe for alerting and wrong for
reconciling against an exchange bill.

**Classification.**

| Condition | `otr_status` | Blocks the order? |
|---|---|---|
| No algo trades executed | `OTR_UNDEFINED_NO_TRADES` | No — but escalate |
| OTR < slab floor (500) | `OTR_NORMAL` | No |
| slab floor ≤ OTR < cooling-off level (2000) | `OTR_PENALTY_SLAB` | No |
| OTR ≥ 2000, fewer than 3 instances in 30 rolling days | `OTR_COOLING_OFF_LEVEL_REACHED` | No |
| OTR ≥ 2000, third or later instance in 30 rolling days | `OTR_COOLING_OFF_TRIGGERED` | No |

**The cooling-off rule, precisely.** SEBI/HO/MRD1/DSAP/CIR/P/2020/107: "On the third
instance of OTR being 2000 or more, in last 30 days (rolling basis), the concerned member
shall not be permitted to place any orders for the first 15 minutes on the next trading
day." One day at 2,000 is a charge under whatever slab the exchange has set. Halting on the
first instance is a self-inflicted outage; treating the third as merely another charge is a
surprise suspension at the next open.

The engine holds no state: pass `prior_cooling_off_instances_30d` — how many **earlier**
days inside the rolling window already recorded an OTR at or above the level — and it adds
today's instance itself. Own that window in a durable store, on calendar days, and age
instances out of it.

**Zero trades.** The ratio is undefined, and the engine says so with `None`. It must never
be replaced with the message count: 400 messages and no fills reported as "400" places the
worst possible day below the 500 slab floor. A day of messages with no fill is separately
exposed to the exchange's quote-stuffing penalty (20 lakh or more order messages with a
trade count of 10 or less, at algo-id + NEAT user-id level per segment) and to the
high-order-messages-with-nil-or-low-trade-count penalty.

**No blocking.** The OTR framework is an economic disincentive on the member. The engine
records it on every report, approved and rejected alike, and never blocks on it — inventing
a pre-trade reject here would be a control SEBI did not impose. Route it to
`order-to-trade-ratio-fee-penalty-avoidance` and to your own throttles.

---

## 7. Audit report generation

Every evaluation returns a `SebiAlgoTaggingReport` carrying:

- the identity fields — `algo_id`, `algo_tag_kind`, `client_category`, `exchange`,
  `segment`, `order_source`;
- a boolean per control — `is_algo_id_valid`, `is_category_valid`,
  `is_ops_within_threshold`, `is_order_type_permitted`, `is_static_ip_compliant`;
- the OTR picture — `chargeable_order_messages`, `calculated_otr_ratio` (`None` when
  undefined), `otr_status`, `otr_cooling_off_instances_30d`,
  `otr_cooling_off_lookback_days`;
- `violations` — **every** breach found, not just the headline; and `status`, the most
  severe by precedence, with `blocks_order`.

Two rules make the record usable after the fact:

1. **Evaluate everything, even after the rejection is certain.** An untagged order at
   50 OPS with a bad category and a third cooling-off instance is not "an untagged order".
2. **Populate the metrics on blocked orders.** A reject path that files OTR as `0.0` no
   longer says what was actually stopped.

Persist to a durable append-only store. Annexure I.a requires audit trail data for IBT /
STWT / Client API / Vendor API orders and trades to be available for **at least 5 years**,
identifying the actual user and user-id. The in-memory report is not that store — see
`record-retention-periods-by-jurisdiction` and
`structured-logging-for-post-incident-forensics`.

---

## 8. What this skill does not cover

- **Black box obligations.** SEBI para 5.V: a black box algo provider must register as a
  Research Analyst, maintain a detailed research report per algo and confirm to the
  exchanges that it is maintained; a change in logic means registering it as a fresh algo.
  Per NSE FAQ Q4, an algo provider cannot host black box algos of multiple third-party RAs.
- **Empanelment.** Criteria are the exchange's (NSE/INVG/70309 para 4.2), and include a
  self-declaration of cyber / adverse technical incidents for the previous 3 years.
- **Hosting.** Annexure I.h and NSE/INVG/69255 Annexure I para 14: retail algos run on the
  broker's servers and order messages originate from them; a tech-savvy client instead
  hosts their own logic at a static IP at their end (NSE FAQ Q5).
- **Mock sessions.** Mandatory monthly mock participation applies to the entities, not to
  the individual tech-savvy client (NSE FAQ Q9).
- **The kill switch.** Exchanges retain the ability to kill orders from a particular algo
  id (para 5.IV(a)(iii)) and to kill rogue algos (Annexure J.3). Your own switch is
  `execution-algorithm-kill-switch-integration`.
- **Rate limiting.** This gate classifies an order against an OPS figure you measured; it
  does not measure or shed flow. See `multi-broker-rate-limit-handling`.
