# Standards for Chaos Engineering Against Trading Infrastructure

## 0. How to read this document

Section 1 lists **regulatory touchpoints** — obligations with the jurisdiction stated.
Sections 2-3 are **engineering standards**: this repository's recommended practice, not
legal requirements, labelled as such so an agent does not present them to an operator as
compliance mandates.

Nothing here substitutes for your own compliance function's determination of which regime
applies to you. Where a number appears, it is an engineering default to be calibrated,
not a threshold any regulator has set.

## 1. EU / UK — Commission Delegated Regulation (EU) 2017/589 ("RTS 6")

**Applicability:** investment firms engaged in algorithmic trading, authorised under
MiFID II (Directive 2014/65/EU); applies from 3 January 2018. It does **not** bind a
US-only broker-dealer, an unregulated proprietary trader outside the EU, or an individual
trading their own capital. The UK retained a materially equivalent onshored version as
assimilated law, supervised by the FCA and supplemented by MAR 7A of the FCA Handbook.

| RTS 6 Article | Subject | Relevance to this skill |
|---|---|---|
| Art. 7 — Testing environments | "An investment firm shall ensure that testing of compliance with the criteria laid down in Article 5(4)(a), (b) and (d) is undertaken in an environment that is separated from its production environment and that is used specifically for the testing and development of algorithmic trading systems and trading algorithms." | This is the regulatory form of "blast radius". A firm in scope does not get to weigh production chaos experiments against the discipline's preference for production realism (§2); the separation is required. |
| Art. 10 — Stress testing | As part of the Art. 9 annual self-assessment, the firm must test that its algorithmic trading systems and the controls in Articles 12-18 "can withstand increased order flows or market stresses", designed for its own activity, and must "ensure that the tests are carried out in such a way that they do not affect the production environment". The tests comprise high messaging volume tests at the highest six-month message count × 2, and high trade volume tests at the highest six-month trading volume × 2. | Volume stress is a **different exercise** from fault injection, and this skill does not perform it — see `load-testing-before-scaling-to-new-instrument-universe`. Cited here for the explicit "do not affect the production environment" constraint and because a resilience programme is normally evidenced through the same annual self-assessment. |
| Art. 12 — Kill functionality | Ability to cancel immediately, as an emergency measure, any or all unexecuted orders at any or all venues. | The kill switch is a **prerequisite** of an experiment, never a subject of one on a live path. Validate it in the separated environment. |
| Art. 14 — Business continuity arrangements | Arrangements appropriate to the nature, scale and complexity of the business, documented in a durable medium (14(1)); adapted per trading venue accessed, covering governance, adverse scenarios, relocation, staff training, kill-functionality policy and position management (14(2)); shutdown must not create disorderly trading conditions (14(3)); and the firm "shall review and test its business continuity arrangements on an annual basis and modify the arrangements in light of that review" (14(4)). | Art. 14(4) is the obligation an automated chaos suite most directly serves: the arrangements must be *tested*, not merely written. Recording each experiment's hypothesis, seed, fault profile and verdict turns that testing into evidence. |

Primary text: Commission Delegated Regulation (EU) 2017/589, EUR-Lex ELI
<https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng>. UK assimilated text:
<https://www.legislation.gov.uk/eur/2017/589>.

**Not verified for this skill and therefore not claimed here:** whether any regulator
mandates fault-injection testing specifically, and any equivalent obligation under
US (SEC/FINRA), Indian (SEBI), Singaporean (MAS) or Australian (ASIC) rules. Treat
resilience testing outside the EU/UK as engineering practice unless your compliance
function establishes otherwise.

## 2. Discipline reference — Principles of Chaos Engineering (engineering practice, not law)

The canonical definition is "the discipline of experimenting on a system in order to
build confidence in the system's capability to withstand turbulent conditions in
production", with four steps: define steady state as a measurable output; hypothesise it
holds in control and experimental groups; introduce variables reflecting real-world
events; try to disprove the hypothesis. Its advanced principles include **blast radius
containment** — "it is the responsibility and obligation of the Chaos Engineer to ensure
the fallout from experiments are minimized and contained" — and **continuous
automation**, since manual experiments are unsustainable.

Reference: <https://principlesofchaos.org/>.

**Where trading diverges.** The same document states that chaos "strongly prefers to
experiment directly on production traffic". That guidance was written for systems whose
worst outcome is a degraded user session. In trading the worst outcome is an unmanaged
open position or a duplicated order, and for RTS 6 firms §1 settles the question. Adopt
steady state, hypothesis, blast radius and automation; do not adopt the production
preference without an explicit, documented risk decision.

## 3. Engineering standards (this repository's recommendation)

| Standard | Requirement |
|---|---|
| Blast radius | Chaos experiments must not be *capable* of interacting with live exchange gateways or real capital: separate credentials, separate endpoints, separate accounts. The injector's fail-closed activation gate (`enabled` / `CHAOS_ENGINEERING_ENABLED`, absent by default) is a backstop against a wrapper left in a shipped code path — not a substitute for that separation. |
| Determinism | Every probabilistic fault profile MUST be seeded, and the seed recorded with the experiment, so a failing run can be replayed exactly. Seeding MUST use generators owned by the harness; re-seeding the process-global RNG changes the behaviour of the system under test (retry backoff, sampling, simulated data) and corrupts the experiment. Independent streams per fault channel let one fault class be disabled on replay without shifting the others. |
| Evidence of injection | An experiment must report what it injected. A run with `faults_injected == 0` is inconclusive, not a pass — at a 10% drop rate, a 20-call run injects nothing about 12% of the time. |
| Grey failures | Resilience testing must include "tarpit" cases — connections that accept data but respond very slowly (seconds to tens of seconds) — to validate timeout budgets, non-blocking I/O and backpressure. A slow-but-open connection bypasses TCP disconnect handling entirely, so it is not covered by kill-the-process tests. |
| Crash fidelity | A simulated crash must not be silently absorbable. `SystemExit` is unsuitable: `threading` discards it in a worker thread without a traceback, and at interpreter level it is indistinguishable from an intentional shutdown. Use a dedicated `BaseException` subclass, which still bypasses `except Exception` while remaining attributable in logs. |
| Ambiguous sends | An experiment that drops an in-flight order send must be evaluated against the recovery rule that the order state is *unknown*, not unsent. Recovery is a state query keyed by client order ID, never a blind resubmission — see `order-placement-idempotency`. |
| Automation | Experiments belong in CI against an integration environment, re-run after every material change to a recovery path, broker adapter or venue session configuration. Quarterly manual exercises validate a system that no longer exists. |
| Scheduling | Do not run experiments inside a deployment freeze window or around a scheduled market event; see `deployment-freeze-windows-around-market-events`. |
