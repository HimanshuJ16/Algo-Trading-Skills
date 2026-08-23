# Standards for Data Pipeline Schema Contract Testing

These are internal engineering conventions for this module. No securities regulator
or market operator mandates a specific schema-validation or null-tolerance figure for
market data ingestion; the numeric thresholds below are **configurable defaults**, not
external requirements. Set them from your own feed's measured baseline.

| Metric | Engineering Standard | Basis |
|---|---|---|
| Ingestion Contract Enforce | ALL incoming market data feeds MUST pass schema contract validation prior to pipeline ingestion. | Internal convention. |
| DLQ Quarantine | Invalid records failing schema validation MUST be routed to a Dead Letter Queue (DLQ), never silently dropped. | Internal convention. |
| Finiteness | `NaN` and `±Inf` MUST be rejected for numeric fields unless the field explicitly opts in via `allow_non_finite`. Bounds checks alone do not catch them: every comparison against `NaN` evaluates False. | IEEE 754 comparison semantics. |
| Boolean Exclusion | `bool` MUST NOT satisfy an `int` or `float` field contract. | Python data model: `bool` is a subclass of `int`. |
| Null Ceiling | Per-field batch null percentage SHOULD be bounded by `max_allowed_null_pct`, expressed on a 0-100 percent scale and applied inclusively. The library default is `1.0` (1%); a stricter value such as `0.5` is a reasonable starting point for critical price/volume fields, but it is a tuning choice, not a mandate. | Tolerance-based null checking mirrors Great Expectations' `mostly` parameter, which is explicitly user-configurable with no recommended threshold. |

## Sources

- Great Expectations — *Manage missing data with GX*: the `mostly` argument sets
  per-expectation null tolerance; the documentation states tolerance levels should be
  adjusted "based on your data patterns and business needs" and prescribes no fixed
  threshold. https://docs.greatexpectations.io/docs/reference/learn/data_quality_use_cases/missingness/
- Great Expectations — `expect_column_values_to_not_be_null`: `mostly` accepts a float
  in [0, 1]; the expectation succeeds if at least that fraction of values are non-null.
  https://greatexpectations.io/legacy/v1/expectations/expect_column_values_to_not_be_null/
- Python Language Reference — `bool` is a subtype of `int`.
  https://docs.python.org/3/library/stdtypes.html#boolean-type-bool
