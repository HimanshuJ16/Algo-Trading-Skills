# Standards for Training Data Freshness SLA

## What is actually mandated, and what is not

**No regulator or standards body prescribes a numeric data-freshness threshold for
model training data.** The 24/36/48-hour defaults in `scripts/training_freshness_sla.py`
are illustrative operating points for a daily-bar equity dataset. They have no external
authority and should be derived from the dataset's own publication cadence — a feed that
publishes once per session cannot meet a threshold shorter than one session, and a
threshold shorter than the longest weekend or exchange holiday will halt a healthy
pipeline on schedule.

What supervisory guidance *does* establish is that data quality and relevance are a
first-class part of model risk management, and that model monitoring must account for
changes in data relevance over time. That is the governance basis for gating a retrain
on dataset freshness; the specific numbers remain a firm-level decision.

## Sources

| Source | Jurisdiction / scope | What it supports | Status |
|---|---|---|---|
| **SR 26-2**, *Supervisory Guidance on Model Risk Management* (Board of Governors of the Federal Reserve System, FDIC, OCC), 17 April 2026 — issued by the OCC as Bulletin 2026-13 | US banking organizations; the letter states it "is expected to be most relevant to banking organizations with over $30 billion in total assets". **Not binding on non-bank proprietary trading firms**, though widely used as the reference framework for model governance. | Model testing "may include a range of activities, from out-of-sample and out-of-time testing, to a comparison of alternative assumptions and methodologies, to **a critical assessment of data quality, relevance, and inputs**" (§IV). Ongoing model monitoring "involves an evaluation of the extent to which a model is performing as expected given potential changes in products, exposures, activities, clients, **data relevance**, or market conditions" (§V). | **Current.** Supersedes and replaces SR 11-7 (4 April 2011) and SR 21-8. Any citation of SR 11-7 for model risk management is out of date as of 17 April 2026. |
| **Google SRE Workbook**, *Implementing SLOs*, "Types of SLIs" | Engineering practice, no jurisdiction. | Establishes **freshness** as one of the canonical SLIs for pipeline systems (alongside correctness and coverage), defined as "the proportion of the data that was updated more recently than some time threshold". This is the vocabulary the skill's target/warning/breach ladder implements. | Current. Non-binding industry practice. |

Scope note from SR 26-2: generative AI and agentic AI models are explicitly outside its
scope; "the principles described in this guidance apply to traditional statistical and
quantitative models and non-generative, non-agentic AI models" (footnote 3). A gradient-
boosted or linear alpha model is in scope for a covered institution; an LLM-based
research assistant is not.

## Engineering standards enforced by this skill

| Standard | Rationale | Enforced how |
|---|---|---|
| Freshness is measured from **event** timestamps, not ingestion timestamps. | Ingestion time omits the vendor's publication delay, so a feed running hours behind appears fresh the moment it lands. | The engine cannot verify which clock produced a timestamp, so it requires the caller to declare `timestamp_basis`. `INGESTION_TIME` is permitted but stamps the caveat into `audit_notes` and the report, keeping the compliance decision auditable rather than silently wrong. |
| Freshness is measured against **expected publication cadence**, not wall clock. | A session-bound dataset is legitimately 65+ hours old on a Monday morning. Wall-clock lag alone produces a recurring false halt, which trains operators to ignore the alert. | `calendar_excluded_hours` is netted out of raw lag before evaluation; both figures are reported. The engine owns no calendar — see `global-exchange-holiday-calendar-handling`. Because the exclusion is a trusted input, an exclusion larger than the elapsed window is rejected, and any pass that depended on the exclusion is flagged `CALENDAR EXCLUSION IS LOAD-BEARING` and logged at WARNING. |
| The gate **fails closed** on unusable input. | Every `>` comparison against NaN is `False`, so a naive threshold ladder returns "compliant" on NaN input. A governance gate that fails open is worse than no gate. | Non-finite timestamps, thresholds and calendar hours raise `ValueError`; wrong types raise `TypeError`. |
| Retraining is halted when lag exceeds the hard SLA limit. | The failure mode this skill exists to prevent: overwriting validated weights with a model fitted to replayed stale prices. | `SLA_BREACH_CRITICAL` returns `config.action_on_breach`, validated at configuration time against `{HALT_MODEL_RETRAINING, REDUCE_CONFIDENCE, ALERT_ONLY}` so an unrecognised string cannot silently defeat the halt. |
| Freshness is evaluated **before every** retraining job, not on a schedule of its own. | A freshness check that ran an hour ago says nothing about the dataset the job is about to read. | The engine is stateless and reads no clock; the caller supplies the evaluation timestamp, so every retrain must evaluate explicitly and every evaluation is replayable from the audit log. |
| Zero lag does not imply completeness. | A backfill can write today's bar and skip the previous four days. | Independent `max_missing_days` and `min_record_count` gates, each reported as its own breach reason. |

## Reference URLs

- SR 26-2: https://www.federalreserve.gov/supervisionreg/srletters/SR2602.pdf
- OCC Bulletin 2026-13: https://www.occ.gov/news-issuances/bulletins/2026/bulletin-2026-13.html
- Google SRE Workbook, Implementing SLOs: https://sre.google/workbook/implementing-slos/
