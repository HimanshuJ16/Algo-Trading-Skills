---
name: japan-fsa-high-speed-trading-registration
description: >-
  Use when automated orders reach Japanese venues from co-located or proximity-hosted
  infrastructure, to classify the activity against the FIEA Article 2(41) high-speed
  trading definition and the Article 66-50 registration requirement.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: regulatory-compliance-global
  tags: japan-fsa, fiea, high-speed-trading, hst-registration, tse, co-location, pre-trade-risk, kill-switch
  brokers_frameworks: "FIEA arts. 2(41)-(42), 29-2(1)(vii), 38(viii), 66-50 to 66-61; Cabinet Office Order on Definitions under FIEA art. 2, art. 26; Cabinet Office Order on Financial Instruments Business arts. 328, 336, 338; FSA Guidelines for Supervision of High-Speed Traders; FSA Notice No. 50 of 2017 designating transmission destinations; TSE Business Regulations art. 14(1)(7); TSE Brokerage Agreement Standards art. 6(5); Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when deploying automated trading systems that send orders to Japanese venues from co-located or proximity-hosted infrastructure. Since **1 April 2018** the Financial Instruments and Exchange Act (FIEA, Act No. 25 of 1948) has required anyone engaging in **high-speed trading** (高速取引行為, FIEA art. 2(41)) to be registered as a **high-speed trader** (高速取引行為者, art. 2(42)) under **FIEA art. 66-50**, unless they are already a registered financial instruments business operator taking the notification route under art. 29-2(1)(vii).

Reach for this skill to classify whether a given order even falls inside the definition, to gate orders on the registration or notification route, and to enforce the per-order obligations that attach once the definition bites — the exchange's high-speed trading flag, the trading strategy type, the kill switch, and the firm's own pre-trade value limits.

## When NOT to Use

- **As evidence of registration.** The engine reads flags the caller supplies. It cannot confirm that a registration is live. The authoritative source is the FSA register of high-speed traders (`https://www.fsa.go.jp/menkyo/menkyoj/kousoku.pdf`), which is the only place to verify a 関東財務局長（高速）第N号 number.
- **For entity-level obligations that do not attach to a single order.** The business method statement (業務方法書), books and records under FIEA art. 66-58, business reports under art. 66-59, and commencement/discontinuance notifications under arts. 66-60 and 66-61 are periodic filings, not per-order checks. This engine deliberately does not model them.
- **Outside Japan.** The FIEA definition is jurisdiction-specific and unusually narrow: it is nothing like MiFID II's "high-frequency algorithmic trading technique". Do not carry these tests into an EU, UK, US, or APAC-ex-Japan gate.
- **As a substitute for the broker's own gate.** Under FIEA art. 38(viii) a financial instruments business operator is prohibited from accepting an entrustment of high-speed trading from an unregistered person, and under Cabinet Office Order on Financial Instruments Business art. 116-4 also from an HST under a business suspension order or one whose trading-system management measures cannot be confirmed. The executing broker will refuse the flow regardless of what this engine returns.

## Prerequisites

- Order and entity payload (`trader_id`, `fsa_hst_reg_id`, `is_registered_with_fsa`, `is_algo_automated`, `is_colocated`, `venue`, `has_contention_free_transmission`, `is_hst_order_flagged`, `trading_strategy_type`, `order_value_jpy`, `has_kill_switch_enabled`, `has_resident_compliance_manager`, `is_foreign_entity`).
- The **current** list of venues designated under Cabinet Office Order on Definitions art. 26(1). The default in `DEFAULT_DESIGNATED_VENUES` is a snapshot dated 2026-06-26 and must be re-verified — the designating FSA notice is amended over time.
- The firm's own calibrated **hard and soft** per-order value limits. The FSA prescribes that such limits exist and are scaled to the trader, not what they should be — see `references/standards.md`.
- Optionally, the strategy types recorded in the entity's 業務方法書, for the `notified_strategy_types` cross-check.

## Workflow

1. **FIEA art. 2(41) Classification** — the definition is **conjunctive and structural, and contains no latency threshold**. An order is high-speed trading only when **all** of the following hold:
   - the decision to trade is made automatically by an electronic data processing system; **and**
   - the order information is transmitted to a **designated** exchange or PTS (Cabinet Office Order on Definitions art. 26(1) plus the FSA designating notice); **and**
   - the order server sits in, adjacent to, or proximate to the facility housing that venue's matching engine (art. 26(2)(i)); **and**
   - a mechanism prevents that transmission from contending with other transmissions — e.g. a contract for exclusive use of a virtual server (art. 26(2)(ii), FSA Guidelines III-3-1-2).

   Resolve missing inputs **conservatively**: a blank `venue` or a `None` `has_contention_free_transmission` is treated as satisfied and raises a warning, so an absent field can never make an in-scope order look out of scope. If the order is not high-speed trading, stop the FSA-specific checks — but keep applying the firm's own value limits, which are a house control and do not switch off.
2. **Registration route — pick the right one before demanding a number.**
   - A registered **financial instruments business operator or registered financial institution** does **not** register as a high-speed trader. It files a notification under FIEA art. 29-2(1)(vii) against its existing registration. Demanding an HST number of a Japanese securities company is a false rejection $\implies$ audit `has_filed_fiea_29_2_notification` instead, else `REJECTED_UNNOTIFIED_FIBO_HST`.
   - Everyone else needs an art. 66-50 registration $\implies$ unregistered gives `REJECTED_UNREGISTERED_HST`; registration claimed without a recorded number gives `REJECTED_MISSING_REGISTRATION_ID`. A number that does not parse as 関東財務局長（高速）第N号 warns rather than rejects — the register is published as text and an unrecognised rendering is not by itself proof of invalidity.
3. **Representative or Agent in Japan** — required of **foreign** applicants only (FIEA art. 66-53(5)(c) and (6)(b)); it is a registration refusal ground, not a universal one. Treat unknown domicile as foreign. The appointee must be able to respond substantively to a regulatory report demand, not merely relay it (FSA Guidelines III-3-1-3(1)(i)(g)) $\implies$ else `REJECTED_NO_JAPAN_REPRESENTATIVE`.
4. **Kill Switch and Pre-Trade Limits** — FSA Guidelines III-2-1-2 requires hard **and** soft limits calibrated to the trader's characteristics and scale, continuous monitoring for anomalous orders, load testing against capacity, and a kill switch able to cancel anomalous orders **already transmitted to the market**. A hard-limit breach rejects; a soft-limit breach warns and lets the order through — that difference is the point of having two limits.
5. **Venue Order Flagging** — TSE Business Regulations art. 14(1)(7) requires an order that constitutes high-speed trading to be indicated as such, and TSE Brokerage Agreement Standards art. 6(5) requires the customer to indicate the trading strategy type on **each** entrustment. Validate the type against `MARKET_MAKING` / `ARBITRAGE` / `DIRECTIONAL` / `OTHER` (FSA Guidelines III-3-1-1(2)(i)) and, where supplied, against the strategies recorded in the 業務方法書.
6. **Audit Report Generation** — every check runs; nothing short-circuits. Output `JapanFsaHstReport` carrying the full `breaches` tuple, the `warnings` tuple, and a `status` set to the most serious breach.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Classifying high-speed trading with a latency threshold.** There is no millisecond figure anywhere in FIEA art. 2(41) or the Cabinet Office Order on Definitions, and the definition deliberately excludes trading *frequency* too. A gate built as `latency_ms <= 20` waves an unregistered co-located automated trader straight through the moment its measured latency drifts above the invented cut-off — a false negative on a criminal registration requirement. Classify on automation plus the two structural transmission legs.
- **Forgetting that the destination venue must be designated.** Only venues named in the FSA notice under Cabinet Office Order on Definitions art. 26(1) count. An order to a venue outside that list is not high-speed trading however co-located and however fast. The list is amended — Osaka Digital Exchange was added on 26 June 2026 — so a hard-coded set silently rots into both false positives and false negatives.
- **Demanding an HST registration number from a securities company.** Financial instruments business operators and registered financial institutions notify under art. 29-2(1)(vii) instead of registering under art. 66-50. They will never have a 関東財務局長（高速）第N号 number, and rejecting their flow for its absence blocks legitimate trading.
- **Treating co-location alone as the second limb.** Cabinet Office Order on Definitions art. 26(2) is a two-part test: the location leg **and** the contention-avoidance leg. Testing only the first over-classifies; testing only the second under-classifies.
- **Applying the Japan-representative requirement to a domestic entity.** It is a refusal ground for foreign corporations and non-resident individuals (art. 66-53(5)(c), (6)(b)). A Japanese-incorporated high-speed trader has no such obligation, and rejecting its orders for a missing "resident compliance manager" is a fabricated requirement.
- **Presenting a house limit as an FSA threshold.** The FSA requires hard and soft pre-trade limits to exist and be calibrated to the firm; it publishes no yen figure. Documenting "JPY 100M limit" as a regulatory mandate is regulatory misinformation, and shipping it as an unreviewed default means nobody ever calibrates it.
- **Sending co-located automated orders without the exchange's high-speed trading flag.** The FSA's own quarterly *Trends in High-Speed Trading* reports note orders placed from co-location servers with no HST identification flag set. The flag and the per-entrustment strategy type are per-order obligations under the TSE rules — exactly the kind of thing a per-order gate should catch, and exactly the kind of thing an entity-level compliance sign-off misses.
- **Reporting a check as passed when it never ran.** A compliance report that hard-codes "pre-trade limit valid" on a path that returned early asserts something the engine never evaluated. An audit trail that lies is worse than one that says "not evaluated".
- **Stopping at the first breach.** An order can be unregistered *and* unflagged *and* over the limit at once. Remediation needs the full list, not the first item.

## Verification

- Instantiate `JapanFsaHstComplianceEngine`. Audit a registered foreign HST (`fsa_hst_reg_id="関東財務局長（高速）第48号"`, `is_registered_with_fsa=True`, `is_algo_automated=True`, `is_colocated=True`, `venue="TSE"`, `has_contention_free_transmission=True`, `is_hst_order_flagged=True`, `trading_strategy_type="MARKET_MAKING"`, `has_kill_switch_enabled=True`) $\implies$ verify `FSA_HST_APPROVED`.
- Confirm latency independence: the same spec at `latency_ms` of 0.5, 20.0, 20.1, 250.0 and 5,000.0 must **all** return `FSA_HST_APPROVED` and `is_hst_classified=True`; the same spec unregistered at `latency_ms=900.0` must still return `REJECTED_UNREGISTERED_HST`.
- Confirm the definition's structural legs: `has_contention_free_transmission=False` or `venue="LSE"` must give `is_hst_classified=False` and `NOT_HIGH_SPEED_TRADING`.
- Confirm the notification route: a FIBO with `has_filed_fiea_29_2_notification=True` and no HST number must return `FSA_HST_APPROVED` with `registration_route="FIEA_29_2_NOTIFICATION"`.
- Confirm per-order obligations: `has_kill_switch_enabled=False` $\implies$ `REJECTED_MISSING_KILL_SWITCH`; `is_hst_order_flagged=False` $\implies$ `REJECTED_MISSING_HST_ORDER_FLAG`; `trading_strategy_type="SCALPING"` $\implies$ `REJECTED_INVALID_TRADING_STRATEGY`.
- Confirm the audit trail is honest: an unregistered order of JPY 900,000,000 against a JPY 100,000,000 hard limit must report `is_pre_trade_limit_valid=False` and carry **both** breaches.
- Run the test suite:
```bash
python -m unittest discover -s skills/japan-fsa-high-speed-trading-registration/scripts
```

## Related Skills

- `japan-exchange-group-jpx-api-integration`
- `hong-kong-sfc-algorithmic-trading-guidelines`
- `mas-singapore-algo-trading-guidelines`
- `execution-algorithm-kill-switch-integration`
- `algo-trading-disclosure-to-exchange-membership`
