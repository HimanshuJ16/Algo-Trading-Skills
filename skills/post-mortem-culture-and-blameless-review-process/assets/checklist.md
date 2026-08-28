# Pre-Flight Checklist — Blameless Post-Mortem Review

## Before writing

- [ ] Is the timeline reconstructed from logs and alerts rather than recollection?
- [ ] Has every responder given their account of what information they had **at the time**, not with hindsight?
- [ ] Is it recorded which alerts fired, which did not, and which fired but were not seen?

## Input hygiene (the engine raises on these)

- [ ] Is `incident_id` non-blank?
- [ ] Is `incident_date` an ISO-8601 `YYYY-MM-DD` calendar date?
- [ ] Are `summary` and `narrative` non-blank?
- [ ] Are `systemic_factors` and `proposed_actions` **lists** of non-blank strings, not single strings?

## Language

- [ ] Are all four sections — summary, systemic factors, narrative, proposed actions — screened, not just the narrative?
- [ ] Has every blocking blame finding been reframed rather than reworded around the detector?
- [ ] Has each counterfactual advisory ("should have", "failed to") been checked: is it describing a **system** or a **person**?
- [ ] If `strict_blame_check=False` was used, has someone actually read the advisory findings?

## Substance

- [ ] Are there at least two systemic factors, and does each name a tool, process, control or architectural gap?
- [ ] Would each factor still hold if any other qualified colleague had been on shift? (If not, it is still about the person.)
- [ ] Does the review go past the trigger to the conditions that let it reach production?
- [ ] Is at least one CAPA item defined, and does each have a named owner and due date **in the tracker** (the engine does not record either)?

## Downstream

- [ ] Is the rendered document stored in the incident record system with the retention period your jurisdiction requires?
- [ ] Are CAPA items tracked to completion rather than closed with the post-mortem?
- [ ] For EU entities: has the DORA Art. 13(2) review covered response promptness, forensic quality, escalation effectiveness and communication effectiveness?
- [ ] Are recurring systemic factors escalated into the periodic control review (RTS 6 Art. 9 annual self-assessment, where applicable)?

## Do not

- [ ] Do not cite a "Google SRE Blameless Standard" or a five-business-day review SLA as a regulatory requirement — neither exists as such. See `references/standards.md`.
- [ ] Do not treat a passing lexical screen as evidence the account is blameless; it removes obvious wording, not blameful framing.
