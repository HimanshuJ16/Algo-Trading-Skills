# Broker Integration Standards — broker-api-changelog-diffing-tool

| Change Category | Change Type | Severity | Action |
|---|---|---|---|
| Removed Path / Method | `REMOVED_ENDPOINT` | `CRITICAL_BREAKING` | Fail CI / Require adapter refactor |
| Removed Parameter | `REMOVED_FIELD` | `HIGH_BREAKING` | Fail CI / Update parameter calls |
| Mutated Parameter Type | `TYPE_MUTATION` | `HIGH_BREAKING` | Fail CI / Update type parsing |
| New Required Parameter | `NEW_REQUIRED_PARAMETER` | `MEDIUM_BREAKING` | Fail CI / Add mandatory argument |
| Added Optional Parameter | `ADDED_OPTIONAL_FIELD` | `NON_BREAKING_INFO` | Pass CI / Log information |

## Category

`broker-integration` — see top-level `mappings/` directory.
