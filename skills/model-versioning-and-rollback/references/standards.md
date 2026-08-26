# Standards for Model Versioning & Rollback

## Engineering requirements

| Requirement | Standard | Source |
|---|---|---|
| Artifact fingerprinting | Every registered version MUST carry a SHA-256 digest: 64 hexadecimal characters, validated by character class and normalised to lowercase before comparison. | NIST FIPS 180-4 |
| Version immutability | A `(model_id, version)` pair MUST map to exactly one artifact for the life of the registry. Re-registration is an idempotent no-op only when the identity fields are identical; otherwise it MUST raise. | Semantic Versioning 2.0.0, rule 3 |
| Version format | Version strings MUST be `X.Y.Z` (non-negative integers, no leading zeroes), optionally `v`-prefixed and optionally carrying a pre-release. Build metadata is rejected. | Semantic Versioning 2.0.0, rules 2, 9, 10 |
| Rollback ordering | Target selection MUST be deterministic. Semver precedence MUST be computed numerically, never by string comparison. | Semantic Versioning 2.0.0, rule 11 |
| Version retention | At least one previously served `PRODUCTION` version MUST remain eligible in the registry, or the breaker has nowhere to go. | Repository mandate |
| Fail-safe evaluation | Non-finite or negatively signed telemetry MUST raise rather than compare. Every comparison against NaN is `False`, which reads as "healthy". | IEEE 754-2019, §5.11 (comparison predicates) |
| Atomicity | The failing version MUST NOT be deactivated before a fallback has been selected, and the swap MUST be performed under a single lock acquisition. | Repository mandate |
| Change record | Every registration, promotion, rollback and halt MUST be recorded with the version, the approver where supplied, and an ordering token. | Repository mandate; see the ESMA briefing below |

## Verified sources

**Semantic Versioning 2.0.0.** <https://semver.org/>

- *Rule 2* — "A normal version number MUST take the form X.Y.Z where X, Y, and Z are non-negative integers, and MUST NOT contain leading zeroes."
- *Rule 3* — "Once a versioned package has been released, the contents of that version MUST NOT be modified." This is the rule the registry's append-only behaviour implements.
- *Rule 11* — "Precedence MUST be calculated by separating the version into major, minor, patch and pre-release identifiers in that order (Build metadata does not figure into precedence)." Major, minor and patch "are always compared numerically"; "when major, minor, and patch are equal, a pre-release version has lower precedence than a normal version."
- *FAQ* — "No, 'v1.2.3' is not a semantic version. However, prefixing a semantic version with a 'v' is a common way (in English) to indicate it is a version number." The engine therefore accepts the prefix and strips it rather than treating it as part of the version.

> Build metadata (`+build`) is **rejected**, not ignored. Rule 10 excludes it from precedence, so `v1.0.0+a` and `v1.0.0+b` would tie — two distinct registry keys of equal rank is an ambiguity a rollback cannot resolve. This is a deliberate narrowing of the spec, not a reproduction of it.

**NIST FIPS 180-4, *Secure Hash Standard (SHS)*, August 2015.** DOI [10.6028/NIST.FIPS.180-4](https://doi.org/10.6028/NIST.FIPS.180-4); PDF <https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.180-4.pdf>

SHA-256 emits a 256-bit digest, hence exactly 64 hexadecimal characters — the basis for the format check. The published digest of the empty string, `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`, is asserted in the test suite as an independent check that `compute_sha256` is doing what it claims.

> **Scope of the guarantee.** A digest gives *integrity* against corruption and accidental substitution. It gives *authenticity* only when the digest itself is protected — an attacker able to overwrite the artifact in object storage can overwrite an unsigned hash stored beside it. Authenticity requires a keyed MAC or a digital signature over the registry record (NIST SP 800-107 Rev. 1 covers the distinction). Claims that a bare hash prevents "tampered deployments" overstate it.

**ESMA, *Supervisory Briefing on Algorithmic Trading in the EU*, ESMA74-1505669079-10311, 26 February 2026.** <https://www.esma.europa.eu/sites/default/files/2026-02/ESMA74-1505669079-10311_Supervisory_Briefing_on_Algorithmic_Trading_in_the_EU.pdf>

- *¶30* — "Testing of an algorithm, algorithmic trading system or algorithmic trading strategy is required following each 'material change' or 'substantial update' thereof," and firms "should manage the risk that a series of minor or small changes due to recalibrations could accumulate over time, when uncontrolled or unchecked, into a material change in the model output without it being tested."
- *¶31* — "A material change or substantial update is any modification that may alter the behaviour, risk profile, or compliance posture of an algorithm, algorithmic trading system or algorithmic trading strategy. **Investment firms are required to timestamp, approve, and record all material changes.**" The briefing's non-exhaustive change-type table includes *Risk Controls — "Changing thresholds, kill switch logic, or alert triggers"*, which places a change to `RollbackTriggerConfig` inside the same regime as a change to the model.
- *¶21* — MiFID II Article 17(2) and RTS 6 Articles 5(4) and 9 "refer to strategies in the context of reporting, testing, documentation, and market abuse surveillance."

> **Status and applicability.** The briefing states that its content "is non-binding and not subject" to a comply-or-explain regime — it is a supervisory convergence tool, not a rule. The underlying obligations sit in MiFID II (Directive 2014/65/EU) Article 17 and RTS 6 (Commission Delegated Regulation (EU) 2017/589), and bind **EU/EEA investment firms engaged in algorithmic trading**. They do not automatically apply to a non-EU proprietary trading firm.
>
> *Sourcing note.* EUR-Lex did not return the operative text of RTS 6 during this review; the RTS 6 article numbers above are as cited **by ESMA in the briefing**, not read from the primary text. Article-level citations for kill functionality (Art. 12) and pre-trade controls (Art. 15) used elsewhere in this repository were not re-verified here and are not relied upon by this skill.

**Federal Reserve SR 26-2, *Revised Guidance on Model Risk Management*, 17 April 2026.** <https://www.federalreserve.gov/supervisionreg/srletters/SR2602.htm>

Issued jointly by the Federal Reserve, OCC and FDIC. It **supersedes and replaces SR 11-7** (April 2011) and SR 21-8 — relevant because SR 11-7 remains the reference most model-governance material still cites.

- *Applicability* — "This letter is expected to be most relevant to banking organizations with over $30 billion in total assets regulated by the Federal Reserve." It is **not** a rule for hedge funds, proprietary trading firms or asset managers, and should not be quoted at them as one.
- *Model Inventory* — "an effective model inventory includes sufficient information to understand model risks, so as to support effective model risk management at the individual and aggregate levels." The revised guidance is principles-based and does **not** enumerate required inventory fields; any claim that a regulator mandates a specific registry schema is unsupported.
- *Ongoing Model Monitoring* — "A model that no longer performs as expected may warrant overlays, adjustment, or redevelopment of the model depending on a banking organization's model risk management policy as it pertains to model deterioration." Note what this does *not* say: no supervisory text identified in this review requires an **automated** rollback. Automating it is an engineering choice about mean-time-to-recovery, not a compliance requirement.

**MLflow Model Registry — stage deprecation.** <https://mlflow.org/docs/2.14.0/model-registry.html>

"As of MLflow 2.9.0, Model Stages have been deprecated and will be removed in a future major release." The replacement is **model version aliases**, which "assign a mutable, named reference to a particular version of a registered model" (`models:/MyModel@champion`), with no fixed state machine.

> This matters directly: `PRODUCTION` / `STAGING` / `ARCHIVED` are MLflow's *legacy* stage names, kept here because they are the vocabulary the rest of this repository uses. If you are building on MLflow today, map this engine's `status` and active pointer onto aliases rather than stage transitions. MLflow's documentation does not describe registered model versions as immutable; the immutability enforced here is this engine's policy, sourced from semver rule 3.

**IEEE 754-2019, *Standard for Floating-Point Arithmetic*.** Comparisons involving NaN are unordered: `NaN > x`, `NaN < x` and `NaN == x` are all false. This is why an unevaluable telemetry sample must raise rather than be compared — the comparison would report "no breach".

## Stated limitations

1. **Single-process, in-memory registry.** The reference engine holds a `threading.RLock`, which makes it safe across threads in one process. It is not a distributed store: two serving hosts with their own instances will disagree about the active version after a rollback. Durable, append-only persistence and cross-host consensus are out of scope.
2. **No trigger hygiene.** One breaching sample acts. There is no confirmation streak, cooldown, per-deployment cap or market-volatility suppression. Those belong to `automated-rollback-triggers-on-anomaly-detection`; feeding this engine raw telemetry will flap.
3. **The pointer is not the deployment.** Moving the active version does not drain in-flight inference, restart a serving process, cancel resting orders, or unwind positions the failing model opened.
4. **Thresholds are yours.** The `15.0` / `5.0` defaults are illustrative placeholders. No regulator, exchange or standard identified in this review specifies a drawdown or inference-error limit for a trading model, and none is implied by their presence here.
5. **No rollback-latency claim is made.** A previous version of this file asserted a "rollback MUST execute within < 100 ms" objective. No source was found for it and none is offered; the figure has been removed rather than re-sourced. Registry mutation here is an in-memory dictionary write, but end-to-end recovery time is dominated by whatever reloads and restarts the serving path, which this engine does not touch or measure.
6. **Validated metrics are taken on trust.** `sharpe_ratio` and `max_drawdown_pct` are accepted as supplied. The engine checks they are finite and non-negative and uses `max_drawdown_pct` to disqualify a fallback that already breaches the live limit; it does not and cannot verify that they were measured honestly. See `backtest-audit-trail-for-regulatory-review`.
7. **`approved_by` is recorded, not authenticated.** The engine logs a warning when it is absent and stores it when present. It performs no authentication, authorisation or segregation-of-duties check — see `risk-control-configuration-change-approval-workflow`.
