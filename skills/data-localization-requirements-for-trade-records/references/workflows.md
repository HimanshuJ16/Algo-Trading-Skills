# Workflows for Data Localization Requirements for Trade Records

1. **Resolve destination jurisdiction**:
   - Map `destination_cloud_region` to the jurisdiction where the region is
     physically located. Never infer it from the region-name prefix: `eu-west-2`
     is London (UK) and `eu-central-2` is Zurich (CH).
   - An unmapped region is unresolved (`REVIEW_REQUIRED`), never approved.
2. **Classify the record**:
   - `MARKET_TICK` is non-personal exchange data; `TRADE_EXECUTION`,
     `CLIENT_PII` and `PAYMENT_LEDGER` carry personal data. The applicable
     regime depends on this classification.
3. **Apply the origin regime**:
   - `CN` — block personal-data egress by default (PIPL Art. 38 mechanism not
     evidenced); flag tick egress for DSL "important data" assessment.
   - `IN` — block offshore payment ledgers (RBI circular); flag other offshore
     Indian trade records for review (SEBI CSCRF PR.DS.S2 in abeyance).
   - `EU` — third-country personal-data storage requires a GDPR Chapter V
     mechanism; it is not a localization violation.
   - `US`/`UK` — no residency mandate; flag destinations whose export controls
     could defeat prompt production under SEC Rule 17a-4(j).
   - Any other origin — `REVIEW_REQUIRED`. Absence of a rule is not permission
     (e.g. Russia's Federal Law 242-FZ is real but not encoded by default).
4. **Verify retention** (US records):
   - Check the declared retention period against the record's 17a-4 paragraph
     (6 years for (a), 3 for (b)) and accept either WORM or the audit-trail
     alternative under 17a-4(f).
5. **Compliance audit**:
   - Persist the returned report; `engine.audit_trail` holds the chronological
     decision record and returns copies so entries cannot be rewritten.
