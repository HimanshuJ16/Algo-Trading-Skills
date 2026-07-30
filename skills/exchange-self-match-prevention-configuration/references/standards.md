# Standards for Exchange Self-Match Prevention (SMP)

| Metric | Engineering Standard |
|---|---|
| Mandatory Tag Header | All automated strategy order messages MUST populate FIX Tag 7928. |
| Supported Instructions | System MUST support `CANCEL_RESTING`, `CANCEL_AGGRESSIVE`, `CANCEL_BOTH`. |
| Wash Trade Zero Tolerance | Pre-trade order checks MUST flag self-collisions prior to exchange entry. |
