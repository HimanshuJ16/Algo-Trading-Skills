# Broker Integration Standards — broker-api-versioning-migration-playbook

| Migration Phase | Target Traffic V1 | Target Traffic V2 | Description |
|---|---|---|---|
| `V1_ONLY` | 100% | 0% | Baseline legacy production operations |
| `SHADOW_MODE` | 100% | 100% (Reads Only) | Schema diff auditing without order risk |
| `CANARY_CUTOVER` | 75% -> 50% -> 0% | 25% -> 50% -> 100% | Incremental live order traffic transition |
| `V2_ONLY` | 0% | 100% | Full migration complete |
| `ROLLBACK_V1` | 100% | 0% | Emergency instant fallback to legacy |

## Category

`broker-integration` — see top-level `mappings/` directory.
