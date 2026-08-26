# Pre-Flight Checklist

## Jurisdiction rules

- [ ] Is a `CorporateResidenceRule` registered for every jurisdiction in play — including any that asserts residence on neither basis?
- [ ] Does each rule record the statute or guidance it came from in `source`?
- [ ] Are individual presence rules registered per jurisdiction rather than assuming a universal 183 days (India's basic test is **182**)?
- [ ] Are weighted tests registered with `Fraction` weights, not floats?
- [ ] Is the US test registered with **both** the 183 weighted-day threshold and the separate 31-day current-year floor?

## Effective management

- [ ] Is `effective_management_country` established from board minutes, travel records, and where decisions are actually taken — not from the registered office?
- [ ] Is it left `None` where it has not been established, rather than guessed?
- [ ] For an India-facing structure, has the "active business outside India" test and the INR 50 crore turnover carve-out been considered?

## Tie-breakers

- [ ] Is the registered tie-breaker the one in **the treaty in force**, checked against any protocol and both parties' MLI positions — not the current OECD Model by default?
- [ ] Is it understood that MLI Art. 4 is not a minimum standard and may have been reserved out of?
- [ ] Does `DUAL_RESIDENCE_UNRESOLVED` block downstream tax booking, on the basis that absent competent-authority agreement the entity may get **no** treaty relief at all?
- [ ] Is a `competent_authority_determination` recorded only where a mutual agreement procedure has actually concluded?

## Individuals

- [ ] Are day counts held per person, per country, per tax year, with no day counted in two countries?
- [ ] Is `meets_registered_test is False` treated as "modelled test not met" rather than as non-residence?
- [ ] Have the unmodelled tests been considered separately — UK sufficient ties, India's 60-plus-365 and 120-day rules, US exempt individuals and the closer connection exception?
- [ ] Where a decision-maker is resident outside the entity's residence country, has the effect on effective management and on dependent agent PE been reviewed?

## Permanent establishment

- [ ] Is every fixed place of business outside the residence country declared in `fixed_places_of_business`?
- [ ] For co-located hardware: is it at the entity's own disposal (owned or leased and operated), or a hosting arrangement?
- [ ] Have the functions performed through it been tested against the preparatory or auxiliary threshold, rather than dismissed because the rack is unmanned?

## Scope boundary

- [ ] Is withholding tax and Foreign Tax Credit work being done in `double-taxation-treaty-considerations-cross-border-trading`, with this engine supplying only the residence country?
- [ ] Is a `None` `resolved_residence_country` blocking that downstream calculation rather than defaulting it?

## Validation

- [ ] Automated testing: `python -m unittest discover -s skills/multi-jurisdiction-tax-residency-implications/scripts`
