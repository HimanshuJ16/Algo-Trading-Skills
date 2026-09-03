# Standards for Algorithmic Trading Firm Licensing Thresholds

All statements below were verified on **2026-09-03** against the sources cited.
Every default in `scripts/algorithmic_trading_firm_licensing_thresholds.py` is
a figure the regulator or the exchange publishes. Where a regime publishes no
number, this file says so rather than supplying one.

## What these regimes do NOT require

| Claim frequently made | Status |
|---|---|
| Rule 15b9-1 exempts a proprietary firm from **broker-dealer registration** | **Wrong statute.** It exempts a broker or dealer from the section 15(b)(8) requirement to become a member of a registered national securities association — FINRA. Registration under section 15(a) is a separate question the rule does not address. |
| The SEC imposes a **message-rate** threshold at which a firm must register | **Does not exist.** Nothing in Rule 15b9-1 turns on order or message rates. The US branch of this skill applies no order-rate test. |
| MiFID II designates a firm as HFT at some tens of messages per second | **Wrong magnitude and wrong statistic.** Article 19 of Delegated Regulation (EU) 2017/565 sets **2** messages/second for a single instrument and **4** across all instruments on a venue, measured on an **average**, not a peak. |
| SEBI's 10 OPS figure is in the SEBI circular | **Not in the circular.** Footnote 2 of SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013 defers the threshold to the Brokers' Industry Standards Forum under the aegis of the exchanges. The number is set by the exchange — NSE/INVG/67858. |
| SEBI's TOPS licenses an algorithmic **trading firm** | **Wrong entity.** It requires a *retail investor's* API-routed **algorithm** to be registered with each exchange, through the broker. |

## US — 17 CFR 240.15b9-1

Source: eCFR, current text; adopted 88 FR 61893, Sept. 7, 2023 (effective
Nov. 6, 2023; compliance one year after Federal Register publication).

The rule exempts a broker or dealer otherwise required by **Exchange Act
section 15(b)(8)** (15 U.S.C. 78o(b)(8)) to join a registered national
securities association, if it meets **all three** conditions:

| Condition | Rule text | Modelled as |
|---|---|---|
| (a) | Is a member of a national securities exchange | `is_exchange_member` |
| (b) | Carries no customer accounts | `has_customers` (checked globally) |
| (c) | Effects transactions in securities solely on a national securities exchange of which it is a member | `off_exchange_volume_usd` net of `exempt_off_exchange_volume_usd` |

Condition (c) retains exactly two exceptions and **no de minimis allowance**:

- **(c)(1)** transactions resulting *solely* from orders routed by a national
  securities exchange of which the firm is a member, to comply with 17 CFR
  242.611 (Reg NMS Rule 611) or the Options Order Protection and
  Locked/Crossed Market Plan.
- **(c)(2)** transactions with or through another registered broker-dealer
  *solely* to execute the stock leg of a stock-option order. A firm relying on
  this must **establish, maintain and enforce written policies and procedures**
  reasonably designed to ensure and demonstrate the transactions were solely
  for that purpose, and preserve a copy consistent with Rule 17a-4 until three
  years after they are replaced.

Because the de minimis allowance is gone, `SEC_OFF_EXCHANGE_FLOOR_USD`
defaults to **0.00 USD** — any non-exempt off-exchange volume defeats
condition (c). The parameter exists so a firm can set its own *triage*
threshold; that is a firm control, not a regulatory carve-out.

## EU — MiFID II high-frequency algorithmic trading technique

Sources: MiFID II (Directive 2014/65/EU) Articles 4(1)(40) and 2(1)(d)(iii);
Commission Delegated Regulation (EU) 2017/565 Article 19; ESMA Q&A on MiFID II
and MiFIR market structures topics (ESMA70-872942901-38), Answers 5 and 30.

| Limb | Threshold | Basis |
|---|---|---|
| Article 19(1)(a) | at least **2 messages per second** for any single financial instrument traded on a trading venue | average, assessed per instrument over that instrument's relevant trading hours (ESMA Q&A A30) |
| Article 19(1)(b) | at least **4 messages per second** across all financial instruments traded on a trading venue | average, summing the per-instrument indicators across the venue (ESMA Q&A A30) |

Article 19(2) restricts the calculation to instruments for which there is a
**liquid market**, per the relevant ESMA publications at the time of
calculation. Messages submitted by DEA clients, and messages for receiving,
transmitting or executing client orders, fall outside the own-account scope of
the test — the caller is responsible for that filtering before supplying the
averages.

ESMA Q&A Answer 5: firms should review their trading activity **at least
monthly** to self-assess whether an authorisation requirement has been
triggered, and on request a trading venue must provide an estimate of the
member's average messages per second within two weeks of each calendar month
end — with the onus remaining on the firm to check that the venue's estimate
reflects its actual activity.

Consequence of meeting either limb: the own-account dealing exemption in
**MiFID II Article 2(1)(d)(iii)** is unavailable and investment firm
authorisation is required.

**Why an order rate cannot substitute.** Two independent mismatches, either of
which is fatal:

1. **Peak versus average.** Article 19 is measured on an average over the
   assessment period. A peak is an upper bound on an average, never a
   substitute for it.
2. **Orders versus messages.** Article 19 counts *messages*, which include
   order modifications and cancellations. A firm submitting one order per
   second and cancel-replacing it several times is already above the
   2 messages/second limb at an order rate of 1.

Consequently `peak_orders_per_second` is not an input to the EU branch at all.
Without both averages the engine returns a `manual_review_items` entry rather
than a clean report — there is no order-rate shortcut to an EU conclusion in
either direction.

## IN — SEBI/exchange Threshold Orders Per Second

Sources: SEBI circular SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013 (Feb. 4,
2025), "Safer participation of retail investors in Algorithmic trading"; NSE
circular NSE/INVG/67858 (implementation standards); SEBI circular
SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/132 (Sept. 30, 2025, timelines).

| Item | Position |
|---|---|
| Who it governs | A **retail investor's** algorithm routed through a broker's API. Brokers are principal, the algo provider or vendor is agent. |
| What is required above the threshold | The **algorithm** must be registered with **each exchange** on which it is used, through the broker. Below the threshold no registration is required, but all algo orders — above and below — carry an exchange-provided unique identifier. |
| Threshold value | Set by the exchange, not by SEBI (circular footnote 2). NSE: Threshold Orders Per Second "initially set at **not exceeding 10 orders per second per exchange**", so the breach condition is **strictly greater than 10**. |
| Measurement basis | The **calendar clock second**, per exchange — not a rolling window. |
| Broker obligation | A broker receiving orders above the threshold shall reject or not process the excess, in accordance with its policy, and may set a stricter per-client limit not exceeding the prescribed TOPS. |
| Timeline | Originally Aug. 1, 2025; deferred to Oct. 1, 2025; the Sept. 30, 2025 circular set a phased glide path with the framework applicable to all stock brokers from **April 1, 2026**. |

Out of scope: a trading member's or proprietary firm's own algorithms answer
to the exchange algo-approval regime, which this module does not model. The
`IN` branch emits a `manual_review_items` entry in that case rather than a
clean report.

## Limitations

- The module screens **quantitative limbs only**. Dealer status, "engaged in
  the business", the scope of an EU investment service, and every other
  qualitative element of registration sit outside it.
- Thresholds change. `evaluated_at` and `schema_version` on each report exist
  so a policy change can be backfilled deterministically against the version
  of the rules that produced it.
- Unrecognised jurisdictions fail closed to manual review; they are never
  silently treated as compliant.
