# Standards — wash-trade-and-spoofing-self-detection

## Legal sources (verified)

| Instrument | What it actually says | Where it is used here |
|---|---|---|
| [CEA s.4c(a)(1) and (a)(2)(A), 7 U.S.C. 6c(a)](https://www.law.cornell.edu/uscode/text/7/6c) | Unlawful to offer to enter into, enter into, or confirm the execution of a transaction that "is, of the character of, or is commonly known to the trade as, a 'wash sale' or 'accommodation trade'; or is a fictitious sale". A wash sale is a transaction entered into without the intent to take a bona fide market position. | Legal basis of the self-match indicator. |
| [CEA s.4c(a)(5)(C), 7 U.S.C. 6c(a)(5)(C)](https://www.law.cornell.edu/uscode/text/7/6c) (added by Dodd-Frank s.747) | Unlawful to engage in conduct on a registered entity that "is, is of the character of, or is commonly known to the trade as, 'spoofing' (bidding or offering with the intent to cancel the bid or offer before execution)". | Legal basis of the layering indicator. |
| [CFTC Antidisruptive Practices Authority interpretive guidance, 78 FR 31890 (28 May 2013)](https://www.federalregister.gov/documents/2013/05/28/2013-12365/antidisruptive-practices-authority) | A market participant must act with **scienter** to violate s.4c(a)(5)(C); reckless trading, practices or conduct do **not** violate the spoofing provision. | Why every output is an alert requiring human analysis, never a finding. |
| [CME Rule 534 — Wash Trades Prohibited](https://www.cmegroup.com/rulebook/files/cme-group-Rule-534.pdf) (Market Regulation Advisory Notice) | Prohibits orders in the same product and expiration (and, for options, strike) entered with the intent to negate or strictly limit market risk. Intent exists where a party **knew or should have known** the orders would achieve a wash result. Globex SMP is a preventive tool, does **not** operate during the pre-open, and is not a defence: self-matching on "more than an incidental basis" may be deemed to violate the rule. | The "knew or should have known" qualifier on the alert text; the SMP boundary in *When NOT to Use*. |
| CME Rule 575 — Disruptive Practices Prohibited | The Globex analogue of the CEA disruptive-practices provisions, covering orders entered with intent to cancel before execution. | `indicator_reference` on layering alerts. |
| [FINRA Rule 5210 Supplementary Material .02 (Self-Trades)](https://www.finra.org/rules-guidance/rulebooks/finra-rules/5210) ([Reg. Notice 14-28](https://www.finra.org/rules-guidance/notices/14-28)) | Self-trades are transactions resulting from the **unintentional interaction of orders originating from the same firm that involve no change in beneficial ownership**. They are generally bona fide; members must have policies reasonably designed to review for and prevent **a pattern or practice** of self-trades from a **single algorithm or trading desk, or related algorithms or desks**. Algorithms within the most discrete unit of an effective system of internal controls are presumed related. | The `strategy_id` relatedness test and the MEDIUM/CRITICAL split. |
| [FINRA Rule 5210 Supplementary Material .03 (Disruptive Quoting and Trading Activity)](https://www.finra.org/rules-guidance/rulebooks/finra-rules/5210) ([Reg. Notice 17-22](https://www.finra.org/rules-guidance/notices/17-22)) | Type 1: entering **multiple** limit orders on one side of the market that change supply and demand, entering one or more orders on the opposite side that are **subsequently executed**, and, **following the execution**, cancelling the original limit orders. Disruptive activity means "a frequent pattern" in which those facts are present. | The `CANCEL_AFTER_FILL` shape, `min_layered_orders`, and the "pattern not instance" caveat. |
| Exchange Act s.9(a)(2) and s.10(b) / Rule 10b-5; Securities Act s.17(a) | The securities statutes do **not** outlaw spoofing by name. The SEC charges spoofing and layering as manipulative practices under the antifraud and market-manipulation provisions. | `indicator_reference` on layering alerts in securities. |
| [Commission Delegated Regulation (EU) 2017/589 (RTS 6), Article 13](https://eur-lex.europa.eu/eli/reg_del/2017/589/oj) — "Automated surveillance system to detect market manipulation" | An investment firm shall monitor all trading activity through its trading systems, including clients', for signs of potential market manipulation as referred to in MAR Article 12, and shall establish and maintain an automated surveillance system that effectively monitors orders and transactions, **generates alerts and reports** and, where appropriate, employs visualisation tools, covering the full range of its trading activity and designed having regard to its nature, scale and complexity. | The obligation this engine partially discharges; the word "alerts" is the regulation's own. |
| [Commission Delegated Regulation (EU) 2017/589 (RTS 6), Article 28](https://eur-lex.europa.eu/eli/reg_del/2017/589/oj) | Records of algorithmic trading in the Annex II format shall be kept for **five years** from submission of the order. | The EU leg of the retention row in the checklist. |
| [Commission Delegated Regulation (EU) 2017/574 (RTS 25)](https://eur-lex.europa.eu/eli/reg_del/2017/574/oj) | MiFID II business-clock accuracy: maximum divergence from UTC of **1 millisecond**, tightening to **100 microseconds** for high-frequency algorithmic trading. | Why naive timestamps are rejected and sub-second lifespan logic is gated on a synchronised clock. |
| [Commission Delegated Regulation (EU) 2017/566 (RTS 9)](https://eur-lex.europa.eu/eli/reg_del/2017/566/oj) | The ratio of unexecuted orders to transactions. The duty sits on the **trading venue**, which must limit the OTR calculated and monitored **per member and per financial instrument**, on the basis of both volumes and numbers. | Why the cancellation ratio here is labelled a firm-side heuristic and not an OTR limit. |
| [Commission Delegated Regulation (EU) 2016/522, Annex II](https://eur-lex.europa.eu/eli/reg_del/2016/522/oj); MAR Annex I Section A | Non-exhaustive indicators of manipulation: layering/spoofing (orders on one side to execute on the other, the unwanted orders then removed) and transactions with **no change in beneficial ownership**. | `indicator_reference` cross-references. |
| [FINRA Regulatory Notice 21-21](https://www.finra.org/rules-guidance/notices/21-21) | The OATS rules (Rule 7400 Series, Rule 4554) were eliminated effective **1 September 2021**; CAT under SEC Rule 613 is the order audit trail. | Correction of the stale OATS reference. |
| FINRA Rule 4511(b); SEA Rule 17a-4(b) | FINRA books and records with no other specified period: **six years**. Information required to be reported to CAT is maintained under SEA Rule 17a-4(b): **three years**, the first two in an accessible place. | The US leg of the retention row. |

### Citation correction

Earlier versions of this skill attributed the wash-trade prohibition to "CFTC Rule 1.38
(7 U.S.C. § 6c(a))". That is wrong on two counts, and the error is preserved here so it is
not reintroduced:

- **17 CFR 1.38 is "Execution of transactions"** — the competitive-execution requirement
  that purchases and sales for future delivery be executed openly and competitively, with
  subsection (b) covering the identification and marking of non-competitive transactions.
  It is not the wash-trade prohibition.
- A CFTC *regulation* (17 CFR) and a *statute* (7 U.S.C.) are not the same instrument, and
  pairing them in one citation implies a provision that does not exist.

The wash-sale prohibition is **CEA s.4c(a)(1) and (a)(2)(A), 7 U.S.C. 6c(a)(1),(2)(A)**.

### Jurisdictional boundaries

- **Futures vs securities diverge on relatedness.** FINRA Rule 5210.02 treats self-trades
  between genuinely unrelated algorithms as generally bona fide. CME Rule 534 has no
  equivalent carve-out and applies a "knew or should have known" test. The engine therefore
  *downgrades* an unrelated-origin self-cross rather than suppressing it.
- **Futures vs securities diverge on the spoofing standard.** CEA s.4c(a)(5)(C) names
  spoofing and requires scienter. The securities statutes do not name it, and the SEC
  proceeds under the antifraud and manipulation provisions.
- **EU output is not a filing.** A suspicion in EU/EEA instruments becomes a STOR under MAR
  Article 16 and Delegated Regulation (EU) 2016/957, through the relevant NCA's own
  channel. This engine produces neither. Since 1 January 2021 the UK applies its own
  assimilated regime supervised by the FCA.
- **Crypto sits outside all of the above.** Neither the CEA spot provisions nor MAR reach
  most crypto-asset venues; the EU analogue is Regulation (EU) 2023/1114 (MiCA) Article 92.

## Detection parameters (calibrate before use)

**No regulator prescribes any of these numbers.** They are library defaults chosen to be
legible. Calibrate each against the instrument's liquidity tier, the venue's microstructure
and the firm's own strategy population, and record the rationale — a threshold you cannot
justify is a threshold you cannot defend.

| Parameter | Default | What it does | Calibration note |
|---|---|---|---|
| `beneficial_owner_map` | `{}` | Account id (or trader id) → beneficial owner id. | Required wherever sub-accounts or multiple desks exist. Without it, ownership is a string comparison. Never inferred. |
| `wash_trade_window_seconds` | `None` | Optional maximum age of a resting order to consider. | `None` is the correct self-match semantic. Any finite value creates false negatives, because an order that has rested for an hour still self-matches. |
| `spoofing_lifespan_threshold_ms` | $1{,}000$ | Window on either side of an execution within which an opposite-side cancellation attaches, and the lifespan below which a cancel counts as fast. | Widen for slower instruments; narrowing raises the burden of proof on the pattern. |
| `layering_size_ratio` | $3.0$ | Withdrawn opposite-side quantity must be at least this multiple of the executed quantity. | The single most important false-positive control. Below about $2.0$ a two-sided market maker trips it on ordinary quote refreshes. |
| `min_layered_orders` | $2$ | Minimum distinct withdrawn orders. | Rule 5210.03 Type 1 says *multiple* limit orders; one withdrawn order is not the shape. |
| `require_related_origin` | `True` | Downgrades a self-cross between two known, different `strategy_id` values to MEDIUM. | Set `False` to treat every self-cross as related — appropriate for a single-desk futures operation under CME Rule 534. |
| `cancellation_ratio_threshold_pct` | $90.0$ | Cancels/placements percentage at or above which the hygiene alert fires, once. | Not an OTR limit (RTS 9 puts that on the venue). A designated market maker meeting quoting obligations will legitimately sit high. |
| `min_orders_for_cancel_ratio` | $10$ | Minimum placements before the ratio is evidence. | 3 of 3 cancelled is $100\%$ on a sample that means nothing. |
| `price_tolerance` | $10^{-9}$ | Absolute tolerance on the crossing test. | Feed venue-tick-aligned prices from the same source that built the book. |
| `max_history_per_owner` | $10{,}000$ | Ring-buffer bound on retained events per owner. | Counters are incremental, so bounding history does not distort the metrics. |
| `max_tracked_event_ids` | $1{,}000{,}000$ | Bound on the duplicate-detection window. | Duplicate rejection is windowed, not absolute: a replay older than this many events is no longer recognised. |

## Detection conditions

### Self-match (wash trade)

Let $O_r$ be a resting own order and $O_i$ the incoming order. The condition is that the
two would **match**, which is a crossing test, not an equality test:

$$\text{owner}(O_i) = \text{owner}(O_r) \;\land\; \text{symbol}_i = \text{symbol}_r \;\land\; \text{side}_i \neq \text{side}_r \;\land\; \text{crosses}(O_i, O_r)$$

$$\text{crosses}(O_i, O_r) = \begin{cases} \text{true} & p_i = \varnothing \ \lor\ p_r = \varnothing \\ p_i \ge p_r - \varepsilon & \text{side}_i = \text{BUY} \\ p_i \le p_r + \varepsilon & \text{side}_i = \text{SELL} \end{cases}$$

An unpriced order ($p = \varnothing$) reaches every level on the opposite side. Where more
than one own order is reachable, the alert names the one the aggressor reaches first: best
price for the aggressor, then earliest placement, then order id — so the alert is
reproducible from the same stream.

### Layering / spoofing

Per (beneficial owner, instrument), for an execution $F$ on side $Y$ with quantity $q_F$,
and the set $C$ of same-owner cancellations on side $X \neq Y$ of orders that were resting
at the moment of $F$, occurring within $\Delta t$ of it:

$$|C| \ge \text{min\_layered\_orders} \;\land\; \sum_{c \in C} q_c \ge \text{layering\_size\_ratio} \times q_F$$

The primary shape, `CANCEL_AFTER_FILL`, requires $0 \le t_c - t_F \le \Delta t$ — the
Rule 5210.03 Type 1 ordering, in which the cancellations *follow* the execution. The
secondary shape, `CANCEL_BEFORE_FILL`, covers $0 \le t_F - t_c \le \Delta t$ and is
reported at lower severity.

### Cancellation ratio

$$R_{\text{cancel}} = \frac{N_{\text{canceled}}}{N_{\text{placed}}} \times 100$$

per beneficial owner, evaluated once $N_{\text{placed}} \ge$ `min_orders_for_cancel_ratio`
and latched after it first fires. This is **not** the RTS 9 order-to-trade ratio.

## Engineering invariants

| Invariant | Why |
|---|---|
| Detection groups by (beneficial owner, instrument). | Grouping by raw trader id dilutes one actor's activity across the accounts it trades and hides cross-account self-crossing. |
| Alert ids are deterministic and derived from the contributing event ids. | `ALT-WASH-<resting_event>-<incoming_event>`, `ALT-LAYER-<shape>-<fill_event>`, `ALT-CXLRATIO-<owner>`. An id derived from a whole-second clock collides and cannot be cited. |
| Every alert carries `requires_human_review=True` and an `indicator_reference`. | Scienter and pattern requirements mean an automated detector cannot reach a finding. |
| A duplicate `event_id` is an error. | A replayed event inflates both the cancellation ratio and the withdrawn-size test. |
| A naive timestamp is an error. | Sub-second lifespan logic on a clock with no defined offset is not defensible (RTS 25). |
| A negative order lifespan is excluded, not averaged. | Averaging it in drags the mean towards zero and manufactures the fast-cancellation signal the metric exists to measure. |
| A rejected event mutates no state. | Validation runs before any counter, book or history write, so a bad event can be corrected and replayed. |
| Threshold alerts latch. | Re-emitting on every subsequent event buries the alert that mattered. |
| Partial fills leave the remainder on the book. | The working residual can still self-match. |

## Known limitations

- **Firm-side view only.** Venue rejects, venue-side modifications and queue position are
  invisible; reconcile against execution reports.
- **Single venue, single instrument key.** No cross-venue or cross-instrument aggregation,
  so manipulation of a derivative through its underlying is out of scope.
- **Two patterns.** Momentum ignition, quote stuffing, ping orders and marking the close
  are not screened here; quote stuffing is covered in
  `eu-market-abuse-regulation-mar-surveillance`.
- **Ownership and relatedness are supplied, never inferred.** The engine cannot discover
  that two accounts share an owner or that two algorithms are related.
- **In-order streaming assumption.** Layering contexts are pruned against the newest event
  timestamp; a materially out-of-order feed must be sequenced upstream.
- **The alert log grows for the life of the engine.** `alerts` is the output; drain and
  persist it.
