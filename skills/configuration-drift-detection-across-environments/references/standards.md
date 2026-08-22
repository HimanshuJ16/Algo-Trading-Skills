# Standards for Configuration Drift Detection

## 0. How to read this document

Section 1 lists **regulatory touchpoints**: obligations that exist in law for firms in
scope, with the jurisdiction stated. Sections 2-5 are **engineering standards** — this
repository's recommended practice, not legal requirements — and are labelled as such so
an agent does not present them to an operator as compliance mandates.

Nothing here substitutes for your own compliance function's determination of which regime
applies to you.

## 1. Regulatory touchpoints

### 1.1 EU / MiFID II — Commission Delegated Regulation (EU) 2017/589 ("RTS 6")

**Applicability:** investment firms engaged in algorithmic trading authorized under MiFID
II (Directive 2014/65/EU). It does **not** bind a US-only broker-dealer, an unregulated
proprietary trader outside the EU, or a retail individual trading their own capital. The
UK operates a materially equivalent onshored version, supervised by the FCA.

| RTS 6 Article | Title | Relevance to configuration drift |
|---|---|---|
| Art. 5(7) | General methodology | The firm must keep records of any material change made to the software used for algorithmic trading, allowing it to determine when the change was made, who made it, who approved it, and the nature of the change. A drift report is evidence of an *undocumented* change — the state this article exists to make impossible. |
| Art. 7 | Testing environments | Testing of the Art. 5(4) criteria must be undertaken in an environment **separated from** the production environment and used specifically for testing and development. Note the direction of the requirement: RTS 6 mandates *separation*, **not** equivalence. Environment parity is an engineering goal (Section 2), not an EU legal obligation — do not tell an operator that RTS 6 requires their staging config to match production. |
| Art. 8 | Controlled deployment of algorithms | Deployment must be controlled, with predefined limits on instruments traded, order price/value/count, positions and venues accessed. Those limits live in configuration; drift in them is drift in the control this article requires. |
| Art. 11 | Management of material changes | A proposed material change to the production environment must be preceded by review by a person designated by senior management, and changes to system functionality must be communicated to the traders in charge of the algorithm and to the compliance and risk management functions. Configuration that reached production without passing this review is exactly what a drift audit surfaces. |

Primary text: Commission Delegated Regulation (EU) 2017/589, EUR-Lex ELI
<https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng>. UK onshored text:
<https://www.legislation.gov.uk/eur/2017/589>.

### 1.2 US — SEC Rule 15c3-5 (Market Access Rule)

**Applicability:** broker-dealers with market access, including those providing it to
customers. It binds the *broker-dealer*, not the strategy author — but it constrains any
configuration that sits on the order path.

- Controls must be reasonably designed to prevent entry of orders exceeding appropriate
  pre-set credit or capital thresholds (para. (c)(1)(i)) and to prevent erroneous orders
  by rejecting those breaching price or size parameters (para. (c)(1)(ii)). Those
  thresholds and parameters are configuration values; a drift audit is one way to
  evidence that the values actually in force are the values that were approved.
- The required controls must be under the **direct and exclusive control** of the
  broker-dealer with market access, subject to the rule's limited allocation exceptions
  in paragraph (d). Consequence for this skill: risk-limit configuration must not be
  something a strategy deployment can quietly override in its own environment.
- The broker-dealer must review the effectiveness of these controls no less frequently
  than annually (para. (e)(1)), with annual CEO certification (para. (e)(2)).

Primary text: 17 CFR 240.15c3-5; adopting release SEC Rel. No. 34-63241
<https://www.sec.gov/files/rules/final/2010/34-63241.pdf>. Staff FAQs:
<https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/divisionsmarketregfaq-0>.

## 2. Engineering standard — when the audit runs

*Recommended practice, not a regulatory requirement.*

The drift audit should execute during process initialization, **before** the trading
socket is opened and before any order is submitted. An audit that runs after the engine
is live reports on a breach rather than preventing one. In a CI/CD pipeline the same
audit should gate promotion, so the failure is caught before the artifact reaches a host
at all.

## 3. Engineering standard — risk parameters are never overrides

*Recommended practice, not a regulatory requirement.*

Risk-control parameters (`max_position_size`, `stop_loss_pct`, `kill_switch_enabled`,
`max_order_usd`, `position_limit`, and their equivalents in your schema) should never be
placed in `allowed_overrides`. A whitelist entry on a risk parameter disables the gate on
precisely the value the gate exists to protect.

`ConfigurationDriftDetector` enforces this: `DEFAULT_PROTECTED_KEYS` is refused in
`allowed_overrides` at construction time, and protected keys are reported CRITICAL at
audit time regardless of the whitelist. **The default list is a starting point keyed on
leaf name, not an inventory of every risk parameter that exists.** Extend it via the
`protected_keys` constructor argument to match your own configuration schema — the
detector cannot know that your `daily_loss_ceiling_inr` is a risk limit.

## 4. Engineering standard — comparison semantics

*Recommended practice, not a regulatory requirement.*

- **Strict type equivalence.** Values whose types differ (`"10"` vs `10`, `True` vs `1`,
  `8` vs `8.0`) should be flagged as drift even when Python considers them equal. A
  threshold parsed as a string from an environment variable, or a kill-switch flag that
  arrives as an int, is a real defect that value-only comparison hides.
- **Ambiguous key paths must fail loudly.** If a literal `"a.b"` key and a nested
  `{"a": {"b": ...}}` both flatten to the same path, one branch would be dropped from the
  audit. Rejecting the config is correct; silently auditing part of it is not.
- **Empty subtrees are still keys.** A configuration section present in one environment
  and absent from the other is drift, even when the section is empty.
- **List comparison is whole-value and order-sensitive.** If ordering is not meaningful
  in your schema (a venue list, say), normalize it before auditing rather than expecting
  the detector to infer that.

## 5. Engineering standard — what a PASS does and does not mean

*Recommended practice, not a regulatory requirement.*

A compliant report means no CRITICAL drift was found under the configured whitelist. It
does **not** mean the two environments are identical:

- Keys present in the target but absent from the baseline are reported as WARNING and do
  not block, because the baseline cannot know what a legitimately-added key means. An
  unreviewed extra key is still a change that reached production; WARNING items should be
  reviewed, not discarded.
- A bare leaf name in `allowed_overrides` whitelists that name **everywhere** in the
  tree. Prefer the exact dot-separated path (`system.api_url`) when the override is meant
  to be scoped to one section.
- The audit compares configuration to configuration. It does not verify that the running
  process actually loaded the file it was given, nor that a value was not mutated after
  startup by an operator, a feature flag service, or a hot reload.
