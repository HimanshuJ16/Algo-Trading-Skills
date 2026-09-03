# Broker & Framework Coverage — kill-switch-and-drawdown-circuit-breakers

**Read this first.** None of the rules below bind an individual or proprietary algo trading
firm to run a *drawdown* or *daily-loss* kill switch. The mandated controls sit upstream, at
the broker-dealer and the exchange. The breaker in `scripts/circuit_breaker.py` is
**defence-in-depth operational risk policy you set for yourself**, and its thresholds are
*your* numbers — do not describe them to a regulator, an auditor, or a user as regulatory
minimums. What the rules below do establish is the *shape* of the control: an emergency
cancel capability, pre-set order-entry limits, and an auditable override path.

| Jurisdiction / Framework | Binds | What it actually requires |
|---|---|---|
| **EU — MiFID II RTS 6, Art. 12** (Reg. (EU) 2017/589, "Kill functionality") | Investment firms engaged in algorithmic trading | Ability to "cancel immediately, as an emergency measure, any or all of its unexecuted orders submitted to any or all trading venues to which the investment firm is connected". Scope is **unexecuted orders** — Art. 12 does *not* mandate flattening open positions. Force-flatten is this skill's own risk policy, not an Art. 12 obligation. |
| **EU — MiFID II RTS 6, Art. 15** (Reg. (EU) 2017/589, "Pre-trade controls on order entry") | Investment firms engaged in algorithmic trading | Price collars, maximum order values, maximum order volumes, and maximum message limits, applied before order entry. Orders that "risk compromising the investment firm's own risk thresholds" must be automatically blocked or cancelled. Any procedure for submitting an order the pre-trade controls blocked must be applied "on a specific trade basis", "on a temporary basis" and "in exceptional circumstances". |
| **US — SEC Rule 15c3-5** (17 CFR 240.15c3-5, Market Access Rule) | A **broker or dealer with market access**, or one providing another person access via its MPID — *not* the end trading firm | (c)(1)(i) reject orders exceeding pre-set credit or capital thresholds in the aggregate; (c)(1)(ii) prevent erroneous orders by rejecting those exceeding price or size parameters "on an order-by-order basis **or over a short period of time**, or that indicate duplicative orders". The (c)(1)(ii) "short period of time" clause is the basis for this module's optional `max_consecutive_rejections` escalation. |
| **India — SEBI, retail algo framework** (Circular SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013, 4 Feb 2025; origin circular CIR/MRD/DP/09/2012, 30 Mar 2012) | Para IV(a)(iii) places the kill-switch obligation on **Exchanges**, not on the broker or the trader | Exchanges shall "continue to have the ability to use the kill switch for orders emanating from a particular algo id". Footnote 4 defines it as "an emergency function and the last level of defence against any Algorithm malfunction… expected to automatically trigger a halt on trading activity based on pre-defined conditions". Brokers carry API/static-IP whitelisting, OAuth + 2FA, algo-ID tagging and audit-trail duties (paras I(d), II(b)) — **not** a daily-loss limit mandate. |

## Applicability caveats

- **Timeline (India):** the 4 Feb 2025 circular was originally effective 1 Aug 2025, deferred
  to 1 Oct 2025 (circular of 29 Jul 2025), then to **full applicability from 1 April 2026**
  (Circular SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/132, 30 Sep 2025).
- **UK:** RTS 6 was assimilated into UK law post-Brexit and is reproduced in the FCA Handbook
  Technical Standards; article numbering is unchanged, but confirm the current UK text
  separately rather than assuming EU/UK parity indefinitely.
- **Not verified here:** no source consulted for this skill imposes a maximum-drawdown or
  daily-loss threshold on a trading firm. If you need a *number* to be defensible, it must
  come from your own risk policy, your broker agreement, or your regulator — not from this
  file. See `risk-limit-calibration-against-historical-drawdowns`.

## Sources

| Claim | Source |
|---|---|
| RTS 6 Art. 12 "Kill functionality" wording and its scope over *unexecuted orders* | Commission Delegated Regulation (EU) 2017/589 (RTS 6), Art. 12 — https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng · FCA Handbook Technical Standards — https://www.handbook.fca.org.uk/techstandards/MIFID-MIFIR/2017/reg_del_2017_589_oj/chapter-ii/section-3/016.html |
| RTS 6 Art. 15 pre-trade controls (price collars, max order value/volume, max messages) and the exceptional-circumstances override procedure | Commission Delegated Regulation (EU) 2017/589 (RTS 6), Art. 15 — https://www.handbook.fca.org.uk/techstandards/MIFID-MIFIR/2017/reg_del_2017_589_oj/chapter-ii/section-3/019.html |
| Rule 15c3-5 applies to a "broker or dealer with market access"; text of (b), (c)(1)(i), (c)(1)(ii) including "over a short period of time" | 17 CFR 240.15c3-5 — https://www.law.cornell.edu/cfr/text/17/240.15c3-5 |
| SEBI circular number/date; kill switch obligation placed on Exchanges (para IV(a)(iii)); footnote-4 definition; broker API/tagging duties | SEBI Circular SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013, 4 Feb 2025 — https://www.sebi.gov.in/legal/circulars/feb-2025/safer-participation-of-retail-investors-in-algorithmic-trading_91614.html |
| Implementation deferred to full applicability from 1 April 2026 | SEBI Circular SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/132, 30 Sep 2025 — https://www.sebi.gov.in/legal/circulars/sep-2025/extension-of-timeline-for-implementation-of-sebi-circular-dated-february-04-2025-on-safer-participation-of-retail-investors-in-algorithmic-trading-_96979.html |
