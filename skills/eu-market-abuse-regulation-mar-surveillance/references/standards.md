# Standards — eu-market-abuse-regulation-mar-surveillance

## Legal sources (verified)

| Instrument | What it actually requires | Where it is used here |
|---|---|---|
| [Regulation (EU) No 596/2014 (MAR), Article 16(1)-(2)](https://eur-lex.europa.eu/eli/reg/2014/596/oj) | Market operators and investment firms operating a trading venue, and persons professionally arranging or executing transactions, must have effective arrangements, systems and procedures to prevent and detect market abuse, and must notify the competent authority **without delay** on reasonable suspicion. | Legal basis stamped on every STOR draft. |
| [Commission Delegated Regulation (EU) 2016/957](https://eur-lex.europa.eu/eli/reg_del/2016/957/oj) | The RTS supplementing MAR Article 16: carries the harmonised **STOR template in its Annex**, requires an appropriate level of **human analysis** alongside automated alerting, and requires the analysis of examined orders and transactions — including those that did **not** result in a STOR, and the reasons — to be retained for **five years** and produced to the competent authority on request. | Template source, `DRAFT_PENDING_HUMAN_REVIEW` status, retention note. |
| [Commission Delegated Regulation (EU) 2016/522, Annex II](https://eur-lex.europa.eu/eli/reg_del/2016/522/oj) | Non-exhaustive indicators of manipulative behaviour supporting MAR Annex I. Layering/spoofing is described as submitting multiple or large orders on one side of the book in order to execute a trade on the other side, the unwanted orders then being removed. Quote stuffing is listed among the same practices. | `indicator_reference` on spoofing and quote-stuffing alerts. |
| MAR Annex I, Section A | Indicators relating to false or misleading signals and price securing — including whether transactions lead to **no change in beneficial ownership**, and orders that change the best bid or offer and are removed before execution. | `indicator_reference` on wash-trade alerts. |
| [Commission Delegated Regulation (EU) 2017/574 (RTS 25)](https://eur-lex.europa.eu/eli/reg_del/2017/574/oj) | MiFID II **business-clock accuracy** — divergence from UTC bounded at 1ms or 100µs depending on the population and gateway-to-gateway latency. **Not** the STOR RTS. | Precondition for sub-100ms lifespan logic. |

### Jurisdictional boundaries

- **EU/EEA only.** Since 1 January 2021 the UK applies its own assimilated MAR and assimilated Delegated Regulation (EU) 2016/957, supervised by the FCA. The FCA is not a National Competent Authority under EU MAR.
- **Financial instruments only.** Crypto-assets sit outside MAR; the analogous detect-and-report obligation is Regulation (EU) 2023/1114 (MiCA) Article 92.
- **No EU-wide submission endpoint.** Each NCA prescribes its own channel and requires enrolment first — BaFin operates a STOR procedure inside its MVP portal (registration required before the first submission); the AMF takes reports through the ROSA extranet with published fallbacks. Nothing in this skill submits anything.

## Configuration defaults (calibrate before use)

**MAR and its RTS prescribe no numeric thresholds** for cancel ratios, order lifespans or message rates. The values below are library defaults chosen to be legible, not standards. Calibrate each against the venue's own microstructure, the instrument's liquidity tier and the participant population, and record the rationale — a threshold you cannot justify is a threshold you cannot defend to an NCA.

| Parameter | Default | What it does | Calibration note |
|---|---|---|---|
| `spoof_cancel_ratio_threshold` | $0.90$ | Fast-cancels / NEW orders per (owner, instrument) at or above which the group is flagged. | Bounded by $1.0$ by construction: only cancels matched to a NEW in the batch count. |
| `spoof_max_lifespan_ms` | $100.0$ | A NEW→CANCEL lifespan at or below this counts as a *fast* cancel. | The discriminator between layering and legitimate quote maintenance. Too high and every liquidity provider trips. |
| `quote_rate_threshold_per_sec` | $500$ | Order-book messages within any one-second window above which a burst is flagged (strict `>`). | Must sit above the quoting rate a designated market maker needs to meet its obligations in that name. |
| `min_orders_for_cancel_ratio` | $5$ | Minimum NEW orders before a ratio is treated as evidence. | 4 of 4 cancelled is $100\%$ on a sample too small to mean anything. |
| `require_opposite_side_fill` | `False` | When `True`, only the strict Annex II shape (opposite-side execution inside the window) alerts. | `True` cuts false positives; it also drops pure layering with no resulting fill. |
| `beneficial_owner_map` | `{}` | Account id → beneficial owner id. | Required wherever sub-accounts exist; without it wash-trade detection is a string comparison. |

## Engineering invariants

| Invariant | Why |
|---|---|
| Detection is per (beneficial owner, instrument). | A batch-wide ratio dilutes a single manipulator inside honest flow, and attributes the alert to whichever instrument happened to appear first. |
| Order lifespan is measured from a matched `NEW`/`CANCEL` pair on `cl_ord_id`. | A cancel whose NEW is outside the batch cannot be timed; it is excluded and counted in `unmatched_cancels`, so truncation under-reports rather than fabricates. |
| Results are independent of input ordering. | Events are sorted internally; two runs over the same batch in different orders produce identical alert ids and text. |
| Alert ids are deterministic. | `ALT_WASH_<event_id>`, `ALT_SPOOF_<owner>_<instrument>`, `ALT_QSTUFF_<owner>_<instrument>` — an id that depended on how many other alerts fired first cannot be cited in an audit trail. |
| A duplicate `event_id` is an error. | A replayed event silently inflates both the cancel ratio and the message rate. |
| No alert is ever marked ready to submit. | Delegated Regulation (EU) 2016/957 requires human analysis; the engine emits `DRAFT_PENDING_HUMAN_REVIEW` with `human_review_required=True`. |

## Known limitations

- **Batch, not streaming.** Patterns straddling a batch boundary are not detected.
- **Three indicators only.** Annex II's list is non-exhaustive and this engine covers three of its practices. Momentum ignition, ping orders, marking the close, cross-venue and cross-instrument manipulation are out of scope.
- **Single-venue view.** Alerts are grouped per instrument key (ISIN, else symbol), with no cross-venue or cross-instrument aggregation.
- **Beneficial ownership is supplied, never inferred.** The engine cannot discover that two accounts share an owner.
