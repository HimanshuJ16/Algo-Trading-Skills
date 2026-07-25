# Standards for Alternative Data Feature Engineering

| Concept | Description | Rule |
|---|---|---|
| **Point-in-Time (PIT)** | The exact time a piece of data became actionable. | All backtesting models must strictly use PIT timestamps, never Event timestamps. |
| **Look-ahead Bias** | Leaking future information into the past. | Forward-filling is only permitted *after* the PIT publication lag has been applied. |
| **Data Revision** | Vendors occasionally update past data files. | If a vendor provides a `revised_date`, the original data must be used until the exact `revised_date` is reached in the simulation. |

## Category
`financial-ml`
