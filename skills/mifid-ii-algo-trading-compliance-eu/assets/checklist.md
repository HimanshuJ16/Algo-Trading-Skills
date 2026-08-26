# Pre-Flight / Sign-off Checklist — mifid-ii-algo-trading-compliance-eu

Use this before considering the skill's implementation complete. Article references are
to Commission Delegated Regulation (EU) 2017/589 (RTS 6).

## Scope

- [ ] **Applicability:** Compliance/legal have confirmed the entity is an investment firm engaged in algorithmic trading under Article 17 of Directive 2014/65/EU, and have separately determined whether a high-frequency algorithmic trading technique is used (triggering Art. 28 order records and RTS 25 clock accuracy).

## Art. 15(1) Pre-Trade Controls

- [ ] **Four controls present:** `validate_pretrade_order()` exercises price collar (a), max order value (b), max order volume (c) and message rate (d), each independently identifiable and testable.
- [ ] **Limits calibrated, not inherited:** Every limit traces to a documented Article 15(4) calibration against the firm's capital base, clearing arrangements, strategy and risk tolerance. The module's placeholder defaults (5% collar, 10 msgs/sec, 100k value, 10k volume) have been replaced. RTS 6 sets no numeric thresholds.
- [ ] **Per-instrument collars:** `price_collar_pct_by_symbol` differentiates between financial instruments as Art. 15(1)(a) requires; instruments that can trade at or below zero use an absolute-tick collar rather than a percentage one.
- [ ] **Signed/invalid sizes rejected:** A negative, zero or non-finite quantity, and a negative notional, are rejected rather than passing the value and volume caps.
- [ ] **Amend/cancel counted:** `record_message("AMEND")` and `record_message("CANCEL")` are wired on the modify and cancel paths, per Art. 15(1)(d).
- [ ] **Override path (Art. 15(6)):** Either no override of a blocked order exists, or it is temporary, exceptional, verified by risk management, authorised by a designated individual, and audited.

## Art. 12 Kill Functionality

- [ ] **Halt and cancel:** `trigger_rts6_kill_switch()` halts new orders AND cancels all resting orders on the venue; the halt is applied before cancellation is attempted.
- [ ] **Manual trigger:** A human can trigger it at any time, independent of any automated condition.
- [ ] **Cancel failure surfaces:** A failing mass-cancel still halts flow, records `cancellation_confirmed=False`, and raises rather than reporting success. Tested with the venue gateway unavailable.
- [ ] **Idempotent cancel:** `cancel_resting_orders_fn` is safe to invoke repeatedly.
- [ ] **Art. 15(3) re-enable:** `reset_kill_switch()` requires an identified operator, is audited, and no automatic path clears the halt.
- [ ] **Usage policy (Art. 14(2)(e)):** Documented — who may trigger, on what evidence, and how re-enabling is authorised.

## Order Attribution

- [ ] **Tagging:** Order payloads include `MiFID2OrderTag` (`algo_id`, `client_id`, `trading_capacity`, short selling, timestamp), attributable per Art. 12(3).
- [ ] **Code sets correct:** Trading capacity is one of `DEAL`/`MTCH`/`AOTC` (RTS 22 Field 29). The short selling boolean is explicitly mapped to the coded indicator (`SESH`/`SSEX`/`SELL`/`UNDI`, Field 62) at the reporting boundary.
- [ ] **Venue-verified:** Tagging checked against each venue's current rulebook and FIX/native specification, not assumed uniform.
- [ ] **Clock (RTS 25):** If HFT — the host clock is traceable to UTC within 100 microseconds at 1 microsecond granularity. A wall-clock timestamp alone is not evidence of this.

## Art. 9 / Art. 28 Audit Trail & Evidence

- [ ] **Durable sink wired:** `audit_sink` writes every pre-trade decision and every kill/reset event to durable, tamper-evident storage. The in-memory `audit_log` ring buffer is not the retention mechanism.
- [ ] **Sink failures alarmed:** `audit_sink_failures` is monitored; a non-zero value means decisions exist only in memory.
- [ ] **Symbol attribution:** Every audit record names the real instrument; no defaulted or sentinel symbol reaches production records.
- [ ] **Retention (Art. 28):** If HFT — order records are captured immediately after submission in the Annex II format and retained five years.

## Testing & Governance (Art. 5–11)

- [ ] **Separated environment (Art. 7):** Testing occurs outside the production environment.
- [ ] **Deployment authorised (Art. 5(2)):** A person designated by senior management authorised this deployment or substantial update.
- [ ] **Change records (Art. 5(7)):** When the change was made, by whom, approved by whom, and its nature are recorded and retrievable.
- [ ] **Conformance testing (Art. 6):** Completed against each venue or DEA provider for this release.
- [ ] **Deployment limits (Art. 8):** Predefined limits set on instruments traded, price/value/number of orders, strategy positions, and number of venues.
- [ ] **Stress testing (Art. 10):** Message-volume and trade-volume tests run at twice the previous six months' peak, without affecting production.
- [ ] **Self-assessment (Art. 9 + Annex I):** Validation report drawn up by the risk management function, audited by internal audit where one exists, approved by senior management.
- [ ] **Business continuity (Art. 14):** Algo-specific plan documented in a durable medium and tested within the last 12 months.

## Automated Testing

- [ ] **Unit tests:** Run `python -m unittest discover -s skills/mifid-ii-algo-trading-compliance-eu/scripts` and confirm a 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
