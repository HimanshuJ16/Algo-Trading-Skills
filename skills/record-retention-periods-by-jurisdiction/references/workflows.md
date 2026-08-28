# Workflow — Resolving a Record's Binding Retention Floor

The order matters. Classifying before looking up, and resolving all jurisdictions before
producing a date, are what stop a wrong number from reaching a purge job.

## 1. Establish the entity's regulatory status — once, not per record

Before any record is assessed, write down which instruments bind the entity: SEC
registration, FINRA membership, CFTC registration, FCA permissions and whether the activity
is MiFID business, MAS CMS licence, Australian incorporation, SEBI registration. Each row
in `references/standards.md` attaches to a status, not to a country.

Override the built-in table wherever a row does not match. A non-FINRA-member US firm, for
example, should override `ORDER_AUDIT_TRAIL` to the three years set by 17a-4(b)(1):

```python
engine = RecordRetentionPeriodsByJurisdictionEngine(
    rules=list(DEFAULT_RETENTION_RULES) + [my_override],
    extension_requested=["UK"],          # only if the FCA has actually asked
)
```

## 2. Classify each record

Map to a `RecordClass` before any jurisdiction lookup:

| Class | Typical contents |
|---|---|
| `TRADE_AND_LEDGER` | Blotters, general ledger, position and account ledgers |
| `ORDER_AUDIT_TRAIL` | Order memoranda, routing and execution audit records |
| `COMMUNICATION` | Electronic and recorded telephone communications |
| `CLIENT_ACCOUNT` | Client account information, onboarding records |
| `OTHER` | Everything else, falling to the jurisdiction's residual floor |

A record that genuinely spans classes is assessed under the longer floor, not split.

## 3. Enumerate every binding jurisdiction

Not the venue's country, and not the booking entity's country — every jurisdiction whose
rules reach the record. Under-listing here is invisible in the output: the engine can only
resolve what it is given.

## 4. Supply the clock start where the rule needs it

If the applicable rule's `clock_start` is not `RECORD_CREATION`, `clock_start_date` is
mandatory. Left out, the record comes back `INDETERMINATE` — deliberately, because
measuring an account record from creation instead of closure understates the obligation by
however long the account was open.

## 5. Assess

```python
report = engine.assess_all(records, as_of=date.today())
```

`as_of` is an explicit argument, never `date.today()` inside the engine, so a report can be
regenerated for a past date and reproduce the same answer.

## 6. Route by status

| Status | Action |
|---|---|
| `RETAIN` | Nothing to do. `days_until_eligible` feeds the next review date. |
| `ELIGIBLE_FOR_REVIEW` | Queue for **human disposition review**, never for automated deletion. |
| `LEGAL_HOLD` | Retain. Record the hold reference alongside the reported floor. |
| `INDETERMINATE` | Retain and fix: add the missing rule, or supply the missing clock start. |

## 7. Feed the storage layer, separately

`readily_accessible_until` is a distinct obligation from the purge date. Pass it to
`data-retention-policy-and-storage-tiering` so a record inside its accessible sub-period is
not tiered into an archive that needs a restore job.

## 8. Act on policy shortfalls before they fire

`policy_shortfall_years` says the firm's own configured duration is below the floor. That
is a configuration change to make now, not an exception to note — the failure it predicts
occurs when the lifecycle rule deletes the record years later.

## 9. Re-verify the table on a schedule

Retention rules change. `references/standards.md` carries a verification date and, for
Singapore and the EU, an explicit note that the primary text was not retrievable during the
last review. Re-check before an examination cycle, and after any change in the entity's
licence or membership.
