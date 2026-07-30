# Standards for Global Macro Calendar Integration

| Metric | Engineering Standard |
|---|---|
| High-Impact Blackout Window | Trading MUST be paused 15 mins before to 15 mins after HIGH_IMPACT events. |
| Order Cancellation Action | Open limit orders MUST be mass-cancelled prior to HIGH_IMPACT releases. |
| Timezone Parity | Calendar timestamps MUST be converted to UTC before audit evaluation. |
