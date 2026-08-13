# Standards for ASIC Market Integrity Rules

Regulatory basis: **ASIC Market Integrity Rules (Securities Markets) 2017**, Part 5.5
(Participant's trading infrastructure) and Part 5.6 (Automated Order Processing -
Filters, conduct, and infrastructure), together with **ASIC Regulatory Guide RG 241**
(Electronic trading, August 2022).

| ASIC Rule | Requirement | Implementation |
|---|---|---|
| **Rule 5.6.1 / 5.6.3(1)(a)-(b)** — Appropriate automated filters | Pre-trade filters must ensure AOP does not interfere with market integrity (RG 241.33-35). | `AsicAopPreTradeFilter` hard-gates every order on value, volume and price deviation; non-compliant orders are rejected, not merely flagged. |
| **Rule 5.6.3(1)(d)-(e)** — Kill switch | Automated controls to suspend/prohibit AOP and suspend or cancel a series of related trading messages (RG 241.54-58). | `AsicKillSwitchManager` is checked before every order; when active, all orders are rejected. Trigger/reset recorded with timestamp, reason and actor. |
| **Rule 5.6.3(2)** — Direct control | Participants must maintain administrator-level direct control over filters and filter parameters (RG 241.47-48). | Filters and config reside on the execution server under participant control, not in the broker's cloud. Config is validated at construction; parameter changes must be governed. |
| **Part 5.6 (recordkeeping)** | Real-time / near-real-time monitoring, exception reporting and post-trade analysis (RG 241.81-87). | Every `ComplianceResult` carries `rejection_code`, `order_id` and `checked_at_unix`; kill switch transitions are appended to an immutable `audit_log`. |

## Filter outcomes (RG 241.35)
A filter may (a) pass a message into the market; (b) pass but flag on exception reports;
(c) pass to a designated trading representative for review; or (d) reject outright.
This skill implements the hard-gate (reject) outcome for breaches; firms may layer
alert/DTR-review outcomes on top, but a breach must never silently pass.

## Category
`regulatory-compliance-global`

## Note on jurisdiction
These rules apply to trading participants on Australian licensed securities markets
(e.g. ASX, CXC, Chi-X Australia). They are not universal; do not apply them to
non-Australian venues without confirming the local regulator's equivalent regime.
