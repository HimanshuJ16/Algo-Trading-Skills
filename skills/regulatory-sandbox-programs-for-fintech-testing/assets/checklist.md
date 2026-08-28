# Pre-Flight Checklist

- [ ] Are every client, volume, AUM and duration cap transcribed from the regulator's
      approval letter — with no invented or "industry standard" defaults?
- [ ] Is the correct program targeted (SEBI **Regulatory** Sandbox for live clients, not
      the offline SEBI Innovation Sandbox)?
- [ ] Is trading volume tracked **cumulatively since test start**, not as open position?
- [ ] Are active clients, cumulative volume, and AUM refreshed at least as often as
      onboarding and trading can move them past a cap?
- [ ] Are pre-breach warnings (default 80% utilisation) routed to a named compliance
      owner rather than an unmonitored dashboard?
- [ ] Is elapsed testing time tracked against the approved duration, with
      `approved_extension_months` set only against a **written** extension grant?
- [ ] Is a client protection / exit-transition plan documented and accepted by the
      regulator, covering normal end, early stop, and extension refusal?
- [ ] Is there a defined escalation path — freeze, notify, transition — for the moment a
      boundary condition is breached?
