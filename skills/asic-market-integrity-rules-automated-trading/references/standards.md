# Standards for ASIC Market Integrity Rules

| ASIC Rule | Requirement | Implementation |
|---|---|---|
| **AOP Pre-Trade Filters** | Orders must not interfere with market integrity. | Hard limits on Value, Volume, and Price Deviation. |
| **Kill Switch** | Ability to immediately halt AOP systems. | `AsicKillSwitchManager` checked prior to every order. |
| **Direct Control** | Participants must maintain control of filters. | Filters reside on the execution server, not just in the broker's cloud. |

## Category
`regulatory-compliance-global`