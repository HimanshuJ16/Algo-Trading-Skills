# Standards for Licensing Thresholds

| Jurisdiction | Rule | Trigger for Mandatory Licensing | Reference Benchmark |
|---|---|---|---|
| **Global** | Customer Funds | Handling or trading customer funds universally triggers broker/advisor licensing. | Customer-trade flag in `FirmTradingActivity`. |
| **US** | SEC Rule 15b9-1 (2023 amendment) | Material proprietary off-exchange trading invalidates the exemption and requires FINRA broker-dealer registration. | `SEC_OFF_EXCHANGE_FLOOR_USD = 1.0` USD by default; per-firm override available. |
| **US (secondary)** | HFT-style activity | Sustained peak OPS consistent with HFT operational profile requires registration review under SEC oversight. | `peak_orders_per_second >= 50` (`MIFID_II_HFT_OPS_LIMIT`) — re-used as US practical HFT signal. |
| **EU** | MiFID II HFT Designation | High message or order rates meet HFT designation and require Investment Firm licensing. | `peak_orders_per_second >= 50` (`MIFID_II_HFT_OPS_LIMIT`). |
| **IN** | SEBI Algo Rules — Retail Limits | Exceeding standard retail automated-trading OPS limits requires formal Algo Registration. | `peak_orders_per_second >= 10` (`SEBI_RETAIL_OPS_LIMIT`). |

## Reference Resolution and Limitations

- The numeric thresholds are practical policy benchmarks for compliance alerting. They are **not** verbatim citations of current SEC, ESMA, or SEBI rule text. Always confirm the active rule text with qualified regulatory counsel before relying on these defaults in production.
- The dataclass `FirmTradingActivity` enforces finite, non-negative metrics at construction. Unrecognized jurisdictions are fail-closed and require manual legal review rather than silently being treated as compliant.
- `LicensingComplianceReport` carries a stable `rule_id`, UTC `evaluated_at`, and `schema_version`, so downstream audit pipelines can reason about report provenance and re-evaluate when policy changes.

## Category
`regulatory-compliance`
