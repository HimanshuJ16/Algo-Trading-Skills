# Pre-Flight / Sign-off Checklist — india-sebi-algo-trading-tagging-requirements

Jurisdiction: **India — SEBI-registered stock brokers, exchange-empanelled algo providers,
and clients trading algorithmically on NSE / BSE / MCX.**

Paragraph references are to SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013 (4 Feb 2025);
Annexure references are to NSE/INVG/67858 (5 May 2025). BSE and MCX issued matching
implementation standards — confirm against the venue you actually trade on.

## Scope — settle this first
- [ ] Channel identified: client API, vendor API, broker algo, member front-end (IBT/STWT), or **DMA**.
- [ ] **If DMA: stop.** Annexure J.1 carves DMA out of these standards entirely. Record that determination and apply the DMA regime instead. An approval from this gate is not DMA compliance.
- [ ] Exchange **and segment** identified — the OPS threshold is per exchange/segment, and the restricted order types differ by segment.
- [ ] Role identified: principal (broker) or agent (algo provider). Para 5.I(a) makes the broker the principal and the provider its agent.
- [ ] Algo categorised white box or black box (para 5.V). If black box, the RA registration and per-algo research report obligations are owned by someone and evidenced.

## Algo ID tagging (para 5.II(b); Annexure G)
- [ ] Every algo order carries an identifier **issued by the Exchange** — not one the broker or client generated.
- [ ] Sub-threshold flow carries the generic Exchange tag (Annexure B.3). "Below the threshold" is not "untagged".
- [ ] Registered algos carry the algorithm ID the exchange issued on registration (Annexure C.2 / D.1 / E.2).
- [ ] Exchange permission held for **each** algo before the facility is offered (para 5.II(a)).
- [ ] Change control wired: any modification to an approved algo goes back to the Exchange for approval (para 5.II(b); Annexure D.3). A logic change is a regulatory event, not just a deploy.
- [ ] NNF tagging verified against the current order-structure protocol for the segment — the exchange identifies an algo by the **13th digit of the 15-digit NNF field** (NSE/CMTR/68802), and validates NNF ID against Algo ID.
- [ ] Digit values used in code are the venue's current ones, not copied from this skill's defaults.

## Threshold Order Per Second (Annexure B.2, B.5, B.6, C.1, F)
- [ ] Current threshold confirmed with the exchange. 10 OPS is "initially set" and adjustable after notice to the market — it is **not** a SEBI number (footnote 2 of the SEBI circular defers it to the ISF).
- [ ] OPS measured **per exchange/segment**, on the flow from client to broker, **on the broker server's calendar clock second** (B.2). Not client-side, not aggregated across venues, not a sliding window.
- [ ] Flow above the threshold is **rejected**, not logged and passed (B.5).
- [ ] Registered algos are not gated by the registration threshold — Section B governs APIs *without* a registered algo.
- [ ] The exactly-at-10 boundary resolved **in writing with the exchange**; the firm's own client-level limit set below it (F permits a lower broker limit).
- [ ] Broker can actually monitor and control OPS for unregistered algos (B.6) — the capability exists, not just the policy.
- [ ] Multiple API keys segregated: unregistered algos on one predefined key only, other keys for registered algos only (A.4).

## Order types
- [ ] Algo **market orders blocked in your own gate**, not left to the exchange's pre-emptive rejection (NSE/SURV/55281; NSE/CMTR/68802).
- [ ] Noted that the capital-market pre-emptive rejection does **not** cover the closing session / post-close — your control must.
- [ ] Commodity segment: IOC also barred for algos (NSE/MSD/67753 §8.1.2.1).
- [ ] Current exchange list of restricted order types / contracts / securities for client algos checked, and the API refuses them (Annexure B.4).

## Access controls (para 5.I(d); Annexure A, I)
- [ ] No open APIs. Access only via a unique vendor-client-specific API key.
- [ ] Static IP whitelisted, and it is the **right party's** IP for the channel: client's for client algos; vendor's or client's for provider algos; broker's or client's for broker algos (A.5). Client static IP is required only for a tech-savvy investor using an API (NSE FAQ Q3, Q6).
- [ ] IP change frequency enforced: not more than once a calendar week, with a documented exception route (A.6).
- [ ] IP-to-client mapping is one-to-one; sharing only within a family as defined in SEBI/HO/MIRSD/MIRSD-PoD1/P/CIR/2024/169, on written or 2FA-validated request (A.7).
- [ ] **OAuth-only** authentication; every other mechanism discontinued.
- [ ] Two-factor authentication on API access.
- [ ] All API sessions compulsorily logged out daily before the next trading day (A.8).
- [ ] Algo providers empanelled with the exchanges, and broker due diligence done before onboarding (para 5.I(d), 5.III(c)).
- [ ] Hosting correct: retail algos on the broker's servers with order messages originating there (Annexure I.h); a tech-savvy client instead hosts their own logic at their static IP.

## PRO/CLI account category
- [ ] Every algo order carries `PRO` or `CLI` correctly.
- [ ] Recorded that this is a **separate** exchange order attribute, not an algo-tagging requirement — the algo circulars are not authority for it, and this review does not substitute for the pro-account controls.

## Order-to-Trade Ratio
- [ ] Understood as a **trading-member, per-segment, per-day** measure over algo orders and algo trades — never per order or per strategy.
- [ ] Exemptions applied, not assumed away: orders within **±0.75% of the LTP**; from **6 Apr 2026**, Designated Market Maker market-making orders and the equity-option premium band; exchange-rejected orders.
- [ ] Documented whether the figure in use is the exchange's billed number or the conservative upper bound the engine computes when no exemptions are supplied.
- [ ] Current slab rates and boundaries taken from **the exchange's own** penalty circular. 500 and 2000 are framework reference levels; the rates are not SEBI's.
- [ ] **Cooling-off modelled correctly**: the suspension applies on the **third** instance of OTR ≥ 2000 within the **last 30 days on a rolling basis**, and stops orders for the **first 15 minutes** of the next trading day (SEBI/HO/MRD1/DSAP/CIR/P/2020/107). One day at 2000 is a charge.
- [ ] The rolling 30-day instance count lives in a durable store, ages out on calendar days, and is fed to the engine — which holds no state.
- [ ] A zero-trade day is reported as **undefined**, never as a ratio equal to the message count.
- [ ] The separate exchange penalties are covered elsewhere: quote stuffing (≥ 20 lakh order messages with ≤ 10 trades, at algo-id + NEAT user-id level), excessive modifications without price/quantity change, and high algo order messages in symbols/contracts with nil or low trade count.
- [ ] Nothing blocks an order on OTR alone. If your system does, that is a control you invented.

## Audit and records
- [ ] **Every** violation recorded per decision, not just the headline status.
- [ ] Approvals recorded as well as rejections.
- [ ] Blocked orders carry the real OTR and control metrics — no zeroed fields on the reject path.
- [ ] Report persisted to a durable append-only store; audit trail data for API orders and trades available for **at least 5 years**, identifying the actual user and user-id (Annexure I.a).
- [ ] Exchange kill switch understood as the exchange's, not yours (para 5.IV(a)(iii); Annexure J.3) — the firm's own kill switch is a separate control.

## Before go-live
- [ ] Six-monthly system audit of the algo trading system scheduled, by an auditor holding CISA, DISA, CISM or CISSP (CIR/MRD/DP/16/2013 paras 2.1–2.2).
- [ ] Mandatory monthly mock sessions covered for the entities (the individual tech-savvy client is exempt — NSE FAQ Q9).
- [ ] Current implementation position re-confirmed against SEBI and the exchange. This framework has been re-phased more than once; full implementation became mandatory for all brokers from **1 April 2026**.
- [ ] Every threshold in the deployed configuration has a named owner, a written derivation and a review date. No number copied from this skill without confirming it.
