# Broker Integration Standards — broker-api-changelog-diffing-tool

Quantitative trading systems are highly sensitive to schema mutations. The following standards dictate CI/CD responses to schema diffs:

| Change Category | Change Type | Severity | Action |
|---|---|---|---|
| Removed Path / Method | `REMOVED_ENDPOINT` | `CRITICAL_BREAKING` | Fail CI / Require adapter refactor |
| Removed Parameter/Request Field | `REMOVED_FIELD` | `HIGH_BREAKING` | Fail CI / Update payload builders |
| Removed Response Field | `REMOVED_RESPONSE_FIELD` | `HIGH_BREAKING` | Fail CI / Fix state machine parsers |
| Mutated Request/Param Type | `TYPE_MUTATION` | `HIGH_BREAKING` | Fail CI / Update type casting |
| Mutated Response Type | `RESPONSE_TYPE_MUTATION` | `HIGH_BREAKING` | Fail CI / Update parser typings |
| Removed Enum Value | `ENUM_MUTATION` | `HIGH_BREAKING` | Fail CI / Update enum definitions |
| New Required Parameter | `NEW_REQUIRED_PARAMETER` | `MEDIUM_BREAKING` | Fail CI / Add mandatory argument |
| Added Optional Parameter | `ADDED_OPTIONAL_FIELD` | `NON_BREAKING_INFO` | Pass CI / Log information |
| Added Endpoint | `ADDED_ENDPOINT` | `NON_BREAKING_INFO` | Pass CI / Log information |

## Category

`broker-integration` — see top-level `mappings/` directory.
