# Workflows for Swiss FINMA Algorithmic Trading Expectations

The procedure below audits one algorithmic trading system operated by a **SIX Swiss
Exchange** participant. Provision references are to FMIO (FinfraV, SR 958.11), the SIX
Trading Rules and Directives, and FINMA Circular 2013/8. See `standards.md` for the
verbatim clause text and the duty chain.

---

## 0. Establish the duty chain before auditing anything

FMIA (FinfraG) contains no algorithmic-trading provision. FMIO Art. 31 does, and it is
addressed to the **trading venue**, which "shall require participants" to comply. The
participant is therefore audited against **its venue's rulebook**, not against the Act.

Practical consequence: fix the venue first. `SwissFINMAComplianceEngine` accepts
`SIX_SWISS_EXCHANGE` only and raises otherwise, because BX Swiss and SDX impose their
own participant rules — BX's published Participant Rules contain no algorithmic-trading
clause at all, so the SIX control set does not transfer.

```python
engine = SwissFINMAComplianceEngine()            # SIX_SWISS_EXCHANGE
engine = SwissFINMAComplianceEngine("BX_SWISS")  # ValueError
```

---

## 1. Collect the audit spec

`AlgoTradingSystemAuditSpec` separates three kinds of input, and the distinction drives
the whole audit:

| Kind | Example | Missing value means |
|---|---|---|
| **Attestation** (`bool`) | `capacity_tested_for_peak_volume` | The control is not in place — a finding. |
| **Evidence pointer** (`str`) | `threshold_calibration_reference` | The control cannot be demonstrated — a finding. |
| **Firm-calibrated setting** (`Optional[float]`) | `max_message_rate_per_sec` | Nothing is configured — a finding. A *value*, whatever its magnitude, is recorded and never judged. |

Booleans must be genuine booleans. `"yes"`, `1`, `0`, `[]` and `None` all raise: a
truthy string would attest to a control nobody assessed, and `True` supplied as a
numeric rate would satisfy any ceiling it were compared against, since `True > 100` is
`False`.

Blank `algo_id` raises. Blank evidence pointers do not — they are the findings.

---

## 2. Identification and notification — SIX Trading Rules cl. 11.1.4 para. 1

Four distinct obligations, easily collapsed into one and then partly missed:

1. **Flag algo-generated orders.** Directive 3 cl. 5.1.3 lit. h no. 1 makes this an
   order *attribute*, listed alongside side, trading capacity, ISIN and validity. It is
   applied per order at entry — verify every order path, not the default one.
2. **A separate identification for each algorithm** (lit. h no. 2). Per algorithm, not
   per firm and not per strategy family. This is what lets the venue satisfy FMIO
   Art. 31 para. 1 lit. b, "the different algorithms used for the creation of orders".
   Parameter variants deployed under a shared identifier defeat it.
3. **Indicate the initiating trader** (lit. h no. 3, FMIO Art. 31 para. 1 lit. c).
4. **Report the operation of algorithmic trading to the Exchange.** An outward
   notification. An internal algorithm inventory, however well maintained, does not
   discharge it.

Whitespace is not evidence: `"   "` is truthy in Python and would pass a naive check on
an identifier field, so `_has_evidence` strips before testing.

---

## 3. Order records including cancellations — cl. 11.1.4 para. 2, FMIO Art. 31 para. 2

The ordinance names cancellations explicitly: "record all entered orders, **including
order cancellations**". These are the records most often dropped by a pipeline that
persists fills and working orders only — and they are exactly what evidences an
order-to-trade ratio and what an Art. 143 FMIA manipulation enquiry would ask for.

The related firm-wide duties (FMIA Art. 38, FMIO Art. 36, FinfraV-FINMA Art. 1 journal
fields) sit outside this audit; see `standards.md`.

---

## 4. Risk controls — FMIO Art. 31 para. 2 lit. a–e / Directive 3 cl. 10 para. 1

| Control | Provision | What the audit checks |
|---|---|---|
| Peak-volume capacity | lit. a | Resilience at peak order and announcement volumes is evidenced. |
| Trading thresholds | lit. b | Thresholds exist **and** carry a recorded calibration basis. |
| Market-abuse prevention | lit. d | Controls exist against Arts. 142/143 FMIA. |
| Algorithm and control testing | lit. e chapeau | Algorithms and control mechanisms are tested. |
| Order-to-trade ratio | lit. e no. 1 | A ratio limit is enabled **and** a value is configured. |
| Order-flow throttling | lit. e no. 2 | Throttling is available **and** a rate is configured. |
| Minimum tick size | lit. e no. 3 | Tick size is limited and enforced. |

Two design decisions worth stating:

- **Thresholds require their calibration record.** Lit. b requires the thresholds to be
  *appropriate*. `has_pre_trade_thresholds=True` with a blank
  `threshold_calibration_reference` fails, because appropriateness that is not written
  down cannot be demonstrated to an auditor. The reverse — a calibration document with
  no thresholds actually deployed — fails for the same reason.
- **Lit. c is not a checkbox.** "Do not cause or contribute to any disruptions in the
  trading venue" is the outcome the other controls serve. An attestation to it would be
  unfalsifiable, so the engine does not collect one.

**No magnitude is ever judged.** Swiss law states no rate, no ratio, no collar and no
notional. `max_message_rate_per_sec=250_000` is compliant if it is what the firm
configured and calibrated. An engine that rejected it while citing FinfraG would be
asserting a rule that does not exist.

---

## 5. Supervisory documentation — FINMA Circular 2013/8 mn 62–63

- mn 62: effective systems and risk controls so algorithmic trading "cannot result in
  any false or misleading signals regarding the supply of, demand for or market price
  of securities".
- mn 63: "document the key features of their algorithmic trading strategies **in a way
  that third parties can understand**." The audit requires both a documentation
  reference and a named `governance_owner`; a document nobody owns is not evidence of
  governance.

---

## 6. Sponsored access and DEA — conditional

Applies **only** when `provides_direct_electronic_access=True`.

- SIX Trading Rules cl. 4.3.4 para. 2: the participant "must be authorised and able at
  any time to delete DEA client orders from the order book on instruction of the
  Exchange".
- SIX Directive 7 cl. 8 para. 2: the Exchange's pre- and at-trade risk management
  controls for Sponsored User flow — "the use of these risk management tools is
  mandatory", and configuring and monitoring them is the Sponsoring Participant's job.
- SIX Directive 7 cl. 8 para. 4: the Exchange-provided **kill switch** over a Sponsored
  User's open orders and order transmission, activatable by the Exchange on request.
- SIX Directive 7 cl. 8 para. 5: assess the controls' effectiveness initially and
  review regularly.

This is the *only* kill switch in the Swiss framework, and it is scoped to client
access. FMIO Art. 31 mentions none. A proprietary-only firm is not an addressee, so the
engine drops `CH_ALGO_14_DEA_ORDER_DELETION` from the applicable set rather than
recording a breach — a fabricated finding in an audit file is as damaging as a missed
one. Note the denominator moves with it: 13 applicable controls without DEA, 14 with.

---

## 7. Read the record

```python
record = engine.audit_algo_system(spec)
if not record.is_compliant:
    for finding in record.failed_controls:
        log.warning(finding)   # each ends with its citation in brackets
```

- `is_compliant` and `failed_controls` are the operative fields.
- `applicable_controls` states what was in scope, which is what makes a partial audit
  interpretable later.
- `citations` maps each in-scope control to its provision, so a finding can be traced
  without leaving the record.
- `finma_score_pct` is an **internal readiness indicator** over the applicable
  controls. Neither FINMA nor SIX publishes a compliance percentage, and a partial
  score is not partial compliance — a participant that cannot identify its algorithms
  is in breach of cl. 11.1.4 at 92%. The record's own `notes` say so.

---

## 8. Retain, and re-audit on change

Persist the record for the audit trail. Re-run the audit whenever an algorithm is
added, materially changed, or given a new parameter variant — a variant deployed under
an existing algorithm identifier is precisely the case cl. 11.1.4 para. 1 is designed
to make visible.

Watch the reform track: the EFD *FinfraG-Review* (30 September 2022) proposes defining
algorithmic trading and high-frequency trading in the Act or ordinance — neither is
defined today — and tightening flagging toward a duty of unique and permanent
algorithm identification on the EU model. The consultation on the FMIA amendment ran
19 June to 11 October 2024. Confirm the current status before relying on the present
drafting.
