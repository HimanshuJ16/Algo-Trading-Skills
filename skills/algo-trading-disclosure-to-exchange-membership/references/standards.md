# Standards for Algo Trading Disclosure

| Rule | Enforcement | Reason |
|---|---|---|
| **Algo ID Presence** | Mandatory for all automated orders | Regulatory audit trail requirements (MiFID II, SEBI). |
| **Algo Versioning** | Major version bumps require new ID | Ensures the exchange knows exactly what logic is running. |
| **Manual Orders** | Must contain `trader_id`, no `algo_id` | Differentiates human vs machine flash-crash liability. |
| **Deprecated Algos** | Hard-rejected by pre-trade risk | Prevents zombie instances from submitting orders after their license expires. |

## Category
`regulatory-compliance`