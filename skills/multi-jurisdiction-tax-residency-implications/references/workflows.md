# Workflows for Multi-Jurisdiction Tax Residency

1. **Register the jurisdiction rules before assessing anything.**
   Build one `MultiJurisdictionTaxResidencyEngine` and register a
   `CorporateResidenceRule` for every jurisdiction in play. Set both flags from
   the law, not from intuition: a jurisdiction that asserts residence on neither
   basis is a legitimate and important registration, and it is what distinguishes
   "the Cayman Islands has no corporate income tax" from "nobody has looked yet".
   Record the statute or guidance in `source` — the audit trail is the point.

2. **Establish the place of effective management as a matter of evidence.**
   `effective_management_country` is where the entity is in fact managed, not
   where the registered office is. For a fund whose directors dial in from three
   countries, the answer lives in board minutes, travel records, and where
   decisions are actually taken. Leave it `None` until it is established; a
   guessed value silently drives the tie-breaker.

3. **Register the tie-breaker the treaty actually contains.**
   Read it off the treaty in force, checked against any protocol and against both
   parties' MLI positions. Register `TIEBREAK_COMPETENT_AUTHORITY` where the 2017
   OECD Model Art. 4(3) or MLI Art. 4 rule applies, and `TIEBREAK_POEM` where the
   treaty still breaks the tie automatically on place of effective management.
   Do not register one because it is the modern rule; register the one that
   governs this pair. An unregistered pair returns `REVIEW_REQUIRED`.

4. **Assess the entity.**
   Call `assess_entity(profile, tax_year, individuals)`. Then branch on `status`:
   - `SINGLE_RESIDENCE` — `resolved_residence_country` is usable downstream.
   - `DUAL_RESIDENCE_RESOLVED` — usable, and the basis is recorded in
     `tie_breaker_method`.
   - `DUAL_RESIDENCE_UNRESOLVED` — **stop**. Open the mutual agreement procedure.
     Until it concludes, the entity may be entitled to no relief or exemption
     under that treaty at all, so no treaty withholding rate may be booked
     against it.
   - `REVIEW_REQUIRED` — a rule or tie-breaker is missing. Register it; do not
     work around it.
   - `NO_RESIDENCE_CLAIMED` — verify this is genuinely the position, and check
     the incorporation jurisdiction's economic substance regime, which may
     require the entity to evidence tax residence somewhere else.

5. **Run the decision-makers through the presence tests.**
   Pass `IndividualPresence` objects for the people who exercise judgement over
   the strategy. Weighted tests need `Fraction` weights — `US_SPT_WEIGHTS` is
   provided — because binary floats shift the answer at the statutory boundary.
   Treat `meets_registered_test is False` as "the modelled test was not met", never
   as non-residence: the ties tests and income-conditioned thresholds that are not
   modelled here can still make the person resident. `None` means no rule was
   registered for that country.

6. **Work the permanent establishment flags.**
   Each flag names a fixed place of business outside the resolved residence
   country. For co-located trading infrastructure the analysis is: is the
   equipment at the entity's own disposal (owned or leased and operated by it,
   rather than a hosting arrangement), is it fixed, and do the functions
   performed through it exceed the preparatory or auxiliary threshold? Absence of
   staff does not settle it.

7. **Hand residence downstream, and stop.**
   Feed `resolved_residence_country` into
   `double-taxation-treaty-considerations-cross-border-trading` as the residence
   country for withholding and Foreign Tax Credit work. This engine computes no
   tax. If `resolved_residence_country` is `None`, the downstream calculation has
   no valid residence country and must not be run on an assumed one.
