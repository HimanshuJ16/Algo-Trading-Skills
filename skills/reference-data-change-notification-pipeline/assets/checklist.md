# Pre-Flight Checklist

## Primary key
- [ ] Is the instrument master keyed on a **persistent identifier** (FIGI, or a CUSIP/ISIN with a documented change policy) rather than on the ticker?
- [ ] Is `instrument_id` the same key that open positions, working orders and historical series are joined on?
- [ ] Do any joins on ticker carry a **date qualifier**, so a recycled symbol cannot splice two issuers into one series?

## Snapshot pair
- [ ] Do `before` and `after` come from the **same source at the same schema version**?
- [ ] Is `after` a **full** snapshot — or is `treat_missing_as_removal=False` set because it is a delta?
- [ ] If running in delta mode, is a periodic full-snapshot diff scheduled so removals are still caught?
- [ ] Are value **types** canonicalized in the loader (`"100"` vs `100` is reported as a change)?
- [ ] Is whitespace and case normalized, so a fixed-width feed's padding does not raise a `CRITICAL` on every instrument?

## Severity configuration
- [ ] Has the `critical_fields` set been audited against the fields your **routing** logic actually reads?
- [ ] Has the `warning_fields` set been audited against the fields your **order construction** actually reads?
- [ ] Is every field consumed by a live system named in one of the two sets — given that an unrecognized field defaults to `INFO` (fail-quiet)?
- [ ] Is `removal_min_severity` set deliberately, rather than inherited?
- [ ] Were the sets re-audited the last time a vendor added or renamed a column?

## Detection behaviour
- [ ] Is presence tested with `in` rather than `dict.get()` anywhere your own code reads these snapshots?
- [ ] Do consumers read `old_present` / `new_present`, rather than inferring absence from a `None` value?
- [ ] Is `ENGINE_DISABLED` handled as a distinct outcome from `NO_CHANGES` in every consumer and dashboard?

## Notification routing
- [ ] Does every consumer have a unique, non-blank name, so a delivery failure can be attributed?
- [ ] Is each consumer's `min_severity` set to what that system can act on, not to `INFO` by default?
- [ ] Is `NotificationDispatchResult.all_delivered` **checked**, and `failures` escalated rather than logged and dropped?
- [ ] Is retry/backoff/dead-lettering implemented in the transport, given that routing attempts each delivery exactly once?
- [ ] Do consumers de-duplicate on `change_key`, so a replay or retry does not double-apply?

## Effective dates
- [ ] Is the application of a detected change **gated on its effective date**, not on detection time?
- [ ] For venue-code changes, is the ISO 10383 publish-vs-effective gap (second Monday vs fourth Monday) accounted for?
- [ ] Is there a check that a detected identity change was not written into the snapshot ahead of its effective date by the loader?

## Alert-storm response
- [ ] If a full-universe `CRITICAL` alert fires, is the **loader** suspected before the exchange?
- [ ] Is there a documented threshold above which a change batch is quarantined for human review rather than auto-applied?

## Sign-off
- [ ] `python -m unittest discover -s skills/reference-data-change-notification-pipeline/scripts` passes.
- [ ] The severity field sets in use are recorded in change control, with the date of the last audit.
