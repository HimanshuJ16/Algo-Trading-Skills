# Pre-Purge Sign-Off Checklist

Complete before any automated process is allowed to delete a record.

## Entity and rule table

- [ ] Has the entity's regulatory status been written down (SEC registration, FINRA
      membership, CFTC registration, FCA permissions and whether the activity is MiFID
      business, MAS CMS licence, Australian incorporation, SEBI registration)?
- [ ] Has counsel confirmed that each built-in row in `references/standards.md` matches
      that status, with overrides supplied where it does not?
- [ ] Is the `ORDER_AUDIT_TRAIL` row correct for this firm — six years for a FINRA member,
      three under 17 CFR 240.17a-4(b)(1) otherwise?
- [ ] If the firm is CFTC-registered, have CFTC Regulation 1.31 rules been added rather
      than the SEC rows reused?
- [ ] Has the competent authority requested the extended five-to-seven-year period under
      SYSC 9.1.2R or MiFID II Art. 16(6), and is `extension_requested` set accordingly?
- [ ] Was `references/standards.md` re-verified within the current review cycle?

## Per-record input

- [ ] Is every record classified into a `RecordClass` before any period is looked up?
- [ ] Does `jurisdictions` list **every** binding jurisdiction, passed as a sequence and
      never as a bare string?
- [ ] Is `clock_start_date` supplied for every record whose rule measures from an event
      other than creation (SEC Rule 17a-4(e)(5): account closure)?
- [ ] Are all dates ISO-8601, with an explicit UTC offset on any datetime?
- [ ] Are `record_id` values unique within the batch?

## Output review

- [ ] Is the count of `INDETERMINATE` records zero — and where it is not, has each been
      resolved by adding a rule or supplying data, rather than by dropping the
      unresolvable jurisdiction?
- [ ] Has every `ELIGIBLE_FOR_REVIEW` record gone to a **human** disposition review rather
      than straight to a purge job?
- [ ] Have current litigation and regulatory holds been reflected in `legal_hold` before
      this run, from the authoritative hold register?
- [ ] Have tax, AML/CFT, contractual, and internal-policy periods been checked separately —
      none of them are modelled here?
- [ ] Is `readily_accessible_until` being honoured by the storage tier, independently of
      the purge date?
- [ ] Has every `policy_shortfall_years` been fixed in configuration, not just noted?
- [ ] Is the report — `as_of`, binding citation, and computed dates — retained as evidence
      of the disposition decision itself?
