"""
eu-market-abuse-regulation-mar-surveillance: batch trade-surveillance engine that
screens order and trade event streams for the manipulative patterns listed in EU
Market Abuse Regulation (MAR) materials and produces a *draft* Suspicious Transaction
and Order Report (STOR) record for human review.

Legal surface (EU / EEA):

- **Regulation (EU) No 596/2014 (MAR), Article 16(1)-(2)** — market operators and
  investment firms operating a trading venue, and persons professionally arranging or
  executing transactions (PPAETs), must have effective arrangements, systems and
  procedures to prevent and detect market abuse, and must notify the competent
  authority *without delay* once reasonable suspicion is formed.
- **Commission Delegated Regulation (EU) 2016/957** — the RTS supplementing MAR
  Article 16. It carries the harmonised STOR template (Annex), requires an appropriate
  level of *human analysis* alongside automated alerting, and requires the analysis
  behind both filed and non-filed cases to be retained for **five years** and produced
  to the competent authority on request.
- **Commission Delegated Regulation (EU) 2016/522, Annex II** — the non-exhaustive
  indicators of manipulative behaviour supporting MAR Annex I. Layering/spoofing is
  described there as submitting orders on one side of the book in order to execute a
  trade on the other side, the unwanted orders then being removed. MAR Annex I
  Section A lists, among its indicators, transactions leading to no change in
  beneficial ownership (the wash-trade indicator) and orders that change the best bid
  or offer and are removed before execution.

What this module is NOT:

- **It is not a filing client.** There is no EU-wide STOR endpoint; each National
  Competent Authority prescribes its own channel and requires prior enrolment (BaFin
  via the MVP portal's STOR procedure; the AMF via the ROSA extranet or its published
  fallbacks). ``stor_filing_payload`` is a draft record, never a submission.
- **It is not a suspicion determination.** Delegated Regulation (EU) 2016/957 requires
  human analysis; an alert here is an input to that analysis. Every alert is emitted
  with ``human_review_required=True``.
- **It is not UK-scoped.** Since 1 January 2021 the UK applies its own assimilated
  version of MAR and of Delegated Regulation (EU) 2016/957, supervised by the FCA. The
  FCA is not a National Competent Authority under *EU* MAR; UK STORs go to the FCA
  under UK MAR. Crypto-assets are outside MAR entirely — the analogous obligation is
  Regulation (EU) 2023/1114 (MiCA) Article 92.

Thresholds are heuristics, not law. Neither MAR nor its RTS prescribes a cancel ratio,
an order lifespan or a message rate. The defaults below are starting points that MUST
be calibrated per venue, instrument liquidity tier and participant population, and the
calibration recorded — see ``references/standards.md``.

Detection scope and limitations (deliberate, documented):

- **Batch, not streaming.** ``audit_events_for_mar_patterns`` scores one finite batch.
  Patterns straddling a batch boundary are not detected; size batches to contain a
  full order lifecycle.
- **Lifespan needs matched lifecycles.** Order lifespan is measured by joining NEW to
  CANCEL on ``cl_ord_id`` within the batch. A CANCEL whose NEW is outside the batch is
  counted in ``unmatched_cancels`` and excluded from the ratio, so a truncated batch
  understates spoofing rather than inventing it.
- **Timestamps must be comparable.** ``timestamp_ns`` is nanoseconds since the Unix
  epoch, UTC. Sub-100ms lifespan logic is only meaningful if the business clocks that
  stamped the events are synchronised — for MiFID II venues and their members that is
  Commission Delegated Regulation (EU) 2017/574 (RTS 25).
- **Beneficial ownership is supplied, not inferred.** Wash trading is an ownership
  test, not a string test. Pass ``beneficial_owner_map`` to collapse sub-accounts onto
  the owning entity; without it, self-execution across two differently named accounts
  of the same owner will not be flagged.
"""
import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

NS_PER_MS = 1_000_000
NS_PER_SEC = 1_000_000_000

#: Event types the engine understands. MODIFY and REJECT are accepted so a complete
#: lifecycle can be passed in; only NEW/MODIFY/CANCEL count as order-book messages.
VALID_EVENT_TYPES = frozenset({"NEW", "MODIFY", "CANCEL", "FILL", "REJECT"})
ORDER_BOOK_EVENT_TYPES = frozenset({"NEW", "MODIFY", "CANCEL"})
VALID_SIDES = frozenset({"BUY", "SELL"})

#: Heuristic defaults. Not regulatory thresholds — calibrate before production use.
DEFAULT_SPOOF_CANCEL_RATIO_THRESHOLD = 0.90
DEFAULT_SPOOF_MAX_LIFESPAN_MS = 100.0
DEFAULT_QUOTE_RATE_THRESHOLD_PER_SEC = 500
DEFAULT_MIN_ORDERS_FOR_CANCEL_RATIO = 5

STOR_TEMPLATE_SOURCE = "Commission Delegated Regulation (EU) 2016/957, Annex (STOR template)"
MAR_LEGAL_BASIS = "Regulation (EU) No 596/2014 (MAR), Article 16(1)-(2)"

#: Draft status. The engine never submits: enrolment and channel are NCA-specific.
STOR_STATUS_DRAFT = "DRAFT_PENDING_HUMAN_REVIEW"

#: Retention obligation carried on every draft so the record is self-describing.
STOR_RETENTION_NOTE = (
    "Delegated Regulation (EU) 2016/957: retain the analysis of orders and transactions "
    "examined — including cases where no STOR was submitted, and the reasons — for five "
    "years and produce it to the competent authority on request."
)


@dataclass
class OrderExecutionEvent:
    """
    One order-lifecycle or execution event.

    ``timestamp_ns`` is nanoseconds since the Unix epoch in UTC.

    ``buyer_account_id`` / ``seller_account_id`` carry both counterparties on a FILL.
    For a resting order (NEW/MODIFY/CANCEL) the owning account is ``account_id`` when
    supplied; otherwise it falls back to the side-appropriate counterparty field
    (buyer for BUY, seller for SELL) so pre-existing event schemas keep working.
    """
    event_id: str
    cl_ord_id: str
    isin: str
    symbol: str
    side: str                           # 'BUY' or 'SELL'
    order_qty: int
    price: float
    event_type: str                     # 'NEW', 'MODIFY', 'CANCEL', 'FILL', 'REJECT'
    timestamp_ns: int                   # Nanoseconds since the Unix epoch, UTC
    buyer_account_id: str
    seller_account_id: str
    account_id: str = ""                # Owning account of the order, when known


@dataclass
class MarSurveillanceAlert:
    """
    One surveillance alert. An alert is an input to human analysis under Delegated
    Regulation (EU) 2016/957 — it is not, by itself, a determination of reasonable
    suspicion and not an instruction to file.
    """
    alert_id: str
    alert_type: str                     # 'WASH_TRADE_ALERT', 'SPOOFING_ALERT', 'QUOTE_STUFFING_ALERT'
    severity: str                       # 'MEDIUM', 'HIGH', 'CRITICAL'
    isin: str
    symbol: str
    details: str
    stor_report_required: bool          # Candidate for STOR assessment — not a filing decision
    account_id: str = ""                # Beneficial owner the pattern is attributed to
    first_event_timestamp_ns: int = 0
    last_event_timestamp_ns: int = 0
    event_ids: Tuple[str, ...] = ()
    indicator_reference: str = ""       # Regulatory indicator the pattern maps to
    metric_value: float = 0.0           # Cancel ratio, peak msgs/s, or filled quantity
    opposite_side_fill_observed: bool = False
    human_review_required: bool = True


@dataclass
class EuMarSurveillanceAuditReport:
    total_events_audited: int
    wash_trade_alerts_count: int
    spoofing_alerts_count: int
    quote_stuffing_alerts_count: int
    alerts: List[MarSurveillanceAlert]
    stor_filing_payload: Optional[Dict[str, str]]
    audit_summary: str
    detection_parameters: Dict[str, str] = field(default_factory=dict)
    unmatched_cancels: int = 0          # CANCELs whose NEW fell outside the batch
    groups_examined: int = 0            # (beneficial owner, instrument) pairs screened


def _instrument_key(event: OrderExecutionEvent) -> str:
    """ISIN where available, else the symbol. One of the two must be present."""
    return event.isin.strip() or event.symbol.strip()


def _validate_events(events: Sequence[OrderExecutionEvent]) -> None:
    """
    Reject malformed input before it can reach a regulatory record.

    Raises:
        TypeError: if an element is not an ``OrderExecutionEvent``.
        ValueError: on an unknown event type or side, a non-positive quantity, a
            non-finite or negative price, a negative timestamp, an event that
            identifies no instrument, or a duplicate ``event_id``.
    """
    seen_event_ids = set()
    for index, event in enumerate(events):
        if not isinstance(event, OrderExecutionEvent):
            raise TypeError(
                f"events[{index}] is {type(event).__name__}, expected OrderExecutionEvent"
            )

        event_type = event.event_type.strip().upper()
        if event_type not in VALID_EVENT_TYPES:
            raise ValueError(
                f"events[{index}] ({event.event_id}): unknown event_type "
                f"{event.event_type!r}; expected one of {sorted(VALID_EVENT_TYPES)}"
            )

        if event.side.strip().upper() not in VALID_SIDES:
            raise ValueError(
                f"events[{index}] ({event.event_id}): unknown side {event.side!r}; "
                f"expected one of {sorted(VALID_SIDES)}"
            )

        if not isinstance(event.order_qty, int) or isinstance(event.order_qty, bool) or event.order_qty <= 0:
            raise ValueError(
                f"events[{index}] ({event.event_id}): order_qty must be a positive int, "
                f"got {event.order_qty!r}"
            )

        if not isinstance(event.price, (int, float)) or isinstance(event.price, bool):
            raise ValueError(
                f"events[{index}] ({event.event_id}): price must be numeric, got {event.price!r}"
            )
        if not math.isfinite(float(event.price)) or float(event.price) < 0.0:
            raise ValueError(
                f"events[{index}] ({event.event_id}): price must be finite and "
                f"non-negative, got {event.price!r}"
            )

        if not isinstance(event.timestamp_ns, int) or isinstance(event.timestamp_ns, bool) or event.timestamp_ns < 0:
            raise ValueError(
                f"events[{index}] ({event.event_id}): timestamp_ns must be a non-negative "
                f"int of nanoseconds since the Unix epoch, got {event.timestamp_ns!r}"
            )

        if not _instrument_key(event):
            raise ValueError(
                f"events[{index}] ({event.event_id}): neither isin nor symbol is set; "
                "a STOR cannot identify the instrument"
            )

        if event.event_id in seen_event_ids:
            raise ValueError(
                f"events[{index}]: duplicate event_id {event.event_id!r} — a repeated "
                "event would double-count in the cancel ratio and the message rate"
            )
        seen_event_ids.add(event.event_id)


class EuMarSurveillanceEngine:
    """
    Screens order/trade batches for wash trading, spoofing/layering and quote stuffing,
    and assembles a draft STOR record for human analysis.

    Thresholds are configurable heuristics, not regulatory values; see the module
    docstring and ``references/standards.md``.
    """

    def __init__(
        self,
        spoof_cancel_ratio_threshold: float = DEFAULT_SPOOF_CANCEL_RATIO_THRESHOLD,
        spoof_max_lifespan_ms: float = DEFAULT_SPOOF_MAX_LIFESPAN_MS,
        quote_rate_threshold_per_sec: int = DEFAULT_QUOTE_RATE_THRESHOLD_PER_SEC,
        min_orders_for_cancel_ratio: int = DEFAULT_MIN_ORDERS_FOR_CANCEL_RATIO,
        beneficial_owner_map: Optional[Mapping[str, str]] = None,
        require_opposite_side_fill: bool = False,
    ) -> None:
        """
        Args:
            spoof_cancel_ratio_threshold: Fast-cancel ratio at or above which a group is
                flagged. Must be in (0.0, 1.0].
            spoof_max_lifespan_ms: A NEW->CANCEL lifespan at or below this counts as a
                fast cancel. Must be positive.
            quote_rate_threshold_per_sec: Order-book messages within any one-second
                window above which quote stuffing is flagged. Must be positive.
            min_orders_for_cancel_ratio: Minimum NEW orders in a group before a ratio is
                meaningful. Must be at least 2.
            beneficial_owner_map: Account id -> beneficial owner id. Accounts absent
                from the map resolve to themselves.
            require_opposite_side_fill: When True, only emit a spoofing alert if an
                opposite-side fill is observed inside the pattern window — the strict
                Delegated Regulation (EU) 2016/522 Annex II layering/spoofing shape.
                When False (default), a high fast-cancel ratio alone is reported at
                MEDIUM severity as a non-bona-fide-order indicator.

        Raises:
            ValueError: on an out-of-range parameter.
        """
        if not math.isfinite(spoof_cancel_ratio_threshold) or not 0.0 < spoof_cancel_ratio_threshold <= 1.0:
            raise ValueError(
                "spoof_cancel_ratio_threshold must be in (0.0, 1.0], got "
                f"{spoof_cancel_ratio_threshold!r}"
            )
        if not math.isfinite(spoof_max_lifespan_ms) or spoof_max_lifespan_ms <= 0.0:
            raise ValueError(f"spoof_max_lifespan_ms must be positive, got {spoof_max_lifespan_ms!r}")
        if quote_rate_threshold_per_sec <= 0:
            raise ValueError(
                f"quote_rate_threshold_per_sec must be positive, got {quote_rate_threshold_per_sec!r}"
            )
        if min_orders_for_cancel_ratio < 2:
            raise ValueError(
                f"min_orders_for_cancel_ratio must be at least 2, got {min_orders_for_cancel_ratio!r}"
            )

        self.spoof_cancel_ratio_threshold = float(spoof_cancel_ratio_threshold)
        self.spoof_max_lifespan_ms = float(spoof_max_lifespan_ms)
        self.quote_rate_threshold_per_sec = int(quote_rate_threshold_per_sec)
        self.min_orders_for_cancel_ratio = int(min_orders_for_cancel_ratio)
        self.beneficial_owner_map: Dict[str, str] = dict(beneficial_owner_map or {})
        for account, owner in self.beneficial_owner_map.items():
            if not str(owner).strip():
                raise ValueError(
                    f"beneficial_owner_map maps account {account!r} to an empty owner id; "
                    "an empty owner would silently merge unrelated accounts into one group"
                )
        self.require_opposite_side_fill = bool(require_opposite_side_fill)

    def resolve_beneficial_owner(self, account_id: str) -> str:
        """Map an account onto its beneficial owner; unmapped accounts map to themselves."""
        account = account_id.strip()
        return self.beneficial_owner_map.get(account, account)

    def _order_owner(self, event: OrderExecutionEvent) -> str:
        """
        Beneficial owner of the order the event belongs to. Prefers the explicit
        ``account_id``; otherwise takes the side-appropriate counterparty field.
        """
        account = event.account_id.strip()
        if not account:
            account = (
                event.buyer_account_id.strip()
                if event.side.strip().upper() == "BUY"
                else event.seller_account_id.strip()
            )
        return self.resolve_beneficial_owner(account)

    def audit_events_for_mar_patterns(
        self, events: Sequence[OrderExecutionEvent]
    ) -> EuMarSurveillanceAuditReport:
        """
        Screen one batch of order and trade events for MAR manipulative patterns.

        Events may arrive in any order; they are sorted internally by timestamp so the
        result does not depend on input ordering. Detection is per (beneficial owner,
        instrument) group, so one participant's behaviour is neither diluted by nor
        confused with the rest of the batch.

        Returns:
            An ``EuMarSurveillanceAuditReport``. Alerts always carry
            ``human_review_required=True``; ``stor_filing_payload`` is a draft record,
            never a submission.

        Raises:
            TypeError, ValueError: as documented on ``_validate_events``.
        """
        _validate_events(events)

        ordered_events = [
            event
            for _, event in sorted(enumerate(events), key=lambda pair: (pair[1].timestamp_ns, pair[0]))
        ]

        wash_alerts = self._detect_wash_trades(ordered_events)
        spoof_alerts, unmatched_cancels, groups_examined = self._detect_spoofing_and_layering(ordered_events)
        quote_alerts = self._detect_quote_stuffing(ordered_events)
        alerts: List[MarSurveillanceAlert] = [*wash_alerts, *spoof_alerts, *quote_alerts]

        detection_parameters = {
            "spoof_cancel_ratio_threshold": f"{self.spoof_cancel_ratio_threshold:.4f}",
            "spoof_max_lifespan_ms": f"{self.spoof_max_lifespan_ms:.3f}",
            "quote_rate_threshold_per_sec": str(self.quote_rate_threshold_per_sec),
            "min_orders_for_cancel_ratio": str(self.min_orders_for_cancel_ratio),
            "require_opposite_side_fill": str(self.require_opposite_side_fill),
            "beneficial_owner_map_entries": str(len(self.beneficial_owner_map)),
        }

        stor_payload = self._build_stor_draft(alerts, detection_parameters) if alerts else None

        summary = (
            f"MAR SURVEILLANCE AUDIT COMPLETE: {len(events)} events processed across "
            f"{groups_examined} (owner, instrument) groups. Alerts: "
            f"Wash={len(wash_alerts)}, Spoof={len(spoof_alerts)}, Quote={len(quote_alerts)}. "
            "All alerts require human analysis before any STOR is filed."
        )
        logger.info(summary)

        return EuMarSurveillanceAuditReport(
            total_events_audited=len(events),
            wash_trade_alerts_count=len(wash_alerts),
            spoofing_alerts_count=len(spoof_alerts),
            quote_stuffing_alerts_count=len(quote_alerts),
            alerts=alerts,
            stor_filing_payload=stor_payload,
            audit_summary=summary,
            detection_parameters=detection_parameters,
            unmatched_cancels=unmatched_cancels,
            groups_examined=groups_examined,
        )

    def _detect_wash_trades(
        self, events: Sequence[OrderExecutionEvent]
    ) -> List[MarSurveillanceAlert]:
        """
        Flag fills with no change in beneficial ownership (MAR Annex I, Section A).

        Sub-accounts are collapsed via ``beneficial_owner_map`` first, so a self-cross
        booked across two account ids of the same owner is caught.
        """
        alerts: List[MarSurveillanceAlert] = []
        for event in events:
            if event.event_type.strip().upper() != "FILL":
                continue
            buyer, seller = event.buyer_account_id.strip(), event.seller_account_id.strip()
            if not buyer or not seller:
                continue
            buyer_owner = self.resolve_beneficial_owner(buyer)
            seller_owner = self.resolve_beneficial_owner(seller)
            if buyer_owner != seller_owner:
                continue

            mapped_note = (
                f" (accounts '{buyer}' / '{seller}' resolved to a common owner)."
                if buyer != seller
                else "."
            )
            instrument_label = (
                f"{event.symbol} ({event.isin})" if event.symbol and event.isin
                else _instrument_key(event)
            )
            details = (
                f"No change in beneficial ownership on {instrument_label} "
                f"(Qty={event.order_qty} @ {event.price}). Beneficial owner "
                f"'{buyer_owner}' on both sides{mapped_note}"
            )
            alerts.append(
                MarSurveillanceAlert(
                    alert_id=f"ALT_WASH_{event.event_id}",
                    alert_type="WASH_TRADE_ALERT",
                    severity="CRITICAL",
                    isin=event.isin,
                    symbol=event.symbol,
                    details=details,
                    stor_report_required=True,
                    account_id=buyer_owner,
                    first_event_timestamp_ns=event.timestamp_ns,
                    last_event_timestamp_ns=event.timestamp_ns,
                    event_ids=(event.event_id,),
                    indicator_reference="MAR Annex I, Section A — no change in beneficial ownership",
                    metric_value=float(event.order_qty),
                )
            )
            logger.critical("WASH_TRADE_ALERT: %s", details)
        return alerts

    def _detect_spoofing_and_layering(
        self, events: Sequence[OrderExecutionEvent]
    ) -> Tuple[List[MarSurveillanceAlert], int, int]:
        """
        Flag high fast-cancel ratios per (beneficial owner, instrument).

        A cancel counts only when its NEW is present in the batch (joined on
        ``cl_ord_id``) and the resulting lifespan is at or below
        ``spoof_max_lifespan_ms``. The ratio is therefore bounded by 1.0 and cannot be
        inflated by cancels of orders the batch never saw; those are reported separately
        as ``unmatched_cancels``.

        Returns:
            ``(alerts, unmatched_cancels, groups_examined)``.
        """
        groups: Dict[Tuple[str, str], Dict[str, object]] = {}

        for event in events:
            event_type = event.event_type.strip().upper()
            if event_type not in {"NEW", "CANCEL", "FILL"}:
                continue
            key = (self._order_owner(event), _instrument_key(event))
            group = groups.setdefault(
                key,
                {"news": {}, "cancels": {}, "fills": [], "isin": event.isin, "symbol": event.symbol},
            )
            side = event.side.strip().upper()
            if event_type == "NEW":
                group["news"].setdefault(event.cl_ord_id, (event.timestamp_ns, side, event.event_id))
            elif event_type == "CANCEL":
                group["cancels"].setdefault(event.cl_ord_id, (event.timestamp_ns, side, event.event_id))
            else:
                group["fills"].append((event.timestamp_ns, side, event.event_id, event.order_qty))

        alerts: List[MarSurveillanceAlert] = []
        unmatched_cancels = 0
        max_lifespan_ns = self.spoof_max_lifespan_ms * NS_PER_MS

        for (owner, instrument), group in sorted(groups.items()):
            news: Dict[str, Tuple[int, str, str]] = group["news"]
            cancels: Dict[str, Tuple[int, str, str]] = group["cancels"]
            unmatched_cancels += sum(1 for cl_ord_id in cancels if cl_ord_id not in news)
            if len(news) < self.min_orders_for_cancel_ratio:
                continue

            fast_cancels = []
            for cl_ord_id, (cancel_ts, _cancel_side, cancel_event_id) in cancels.items():
                if cl_ord_id not in news:
                    continue
                new_ts, new_side, new_event_id = news[cl_ord_id]
                lifespan_ns = cancel_ts - new_ts
                if lifespan_ns < 0:
                    logger.debug(
                        "Clock anomaly on %s: CANCEL %s precedes its NEW %s by %dns; excluded "
                        "from the fast-cancel ratio",
                        cl_ord_id, cancel_event_id, new_event_id, -lifespan_ns,
                    )
                    continue
                if lifespan_ns <= max_lifespan_ns:
                    fast_cancels.append((new_ts, cancel_ts, new_side, new_event_id, cancel_event_id))

            if not fast_cancels:
                continue
            cancel_ratio = len(fast_cancels) / float(len(news))
            if cancel_ratio < self.spoof_cancel_ratio_threshold:
                continue

            window_start = min(entry[0] for entry in fast_cancels)
            window_end = max(entry[1] for entry in fast_cancels)
            cancelled_sides = {entry[2] for entry in fast_cancels}
            opposite_sides = VALID_SIDES - cancelled_sides
            opposite_fill = next(
                (
                    fill
                    for fill in group["fills"]
                    if fill[1] in opposite_sides and window_start <= fill[0] <= window_end
                ),
                None,
            )

            if self.require_opposite_side_fill and opposite_fill is None:
                logger.debug(
                    "Spoofing candidate suppressed for %s/%s: %.1f%% fast-cancel ratio but no "
                    "opposite-side fill inside the window (require_opposite_side_fill=True)",
                    owner,
                    instrument,
                    cancel_ratio * 100.0,
                )
                continue

            event_ids = tuple(entry[3] for entry in fast_cancels) + tuple(entry[4] for entry in fast_cancels)
            if opposite_fill is not None:
                severity = "HIGH"
                fill_note = (
                    f" Opposite-side {opposite_fill[1]} fill {opposite_fill[2]} "
                    f"(Qty={opposite_fill[3]}) executed inside the pattern window."
                )
                event_ids += (opposite_fill[2],)
            else:
                severity = "MEDIUM"
                fill_note = (
                    " No opposite-side fill observed inside the window: consistent with orders "
                    "placed without intention to execute, not confirmed spoofing."
                )

            lifespans_ms = [(entry[1] - entry[0]) / NS_PER_MS for entry in fast_cancels]
            details = (
                f"Layering/spoofing pattern for beneficial owner '{owner}' on {instrument}: "
                f"{cancel_ratio * 100:.1f}% of {len(news)} orders cancelled within "
                f"{self.spoof_max_lifespan_ms:.0f}ms (mean lifespan "
                f"{sum(lifespans_ms) / len(lifespans_ms):.3f}ms) on side(s) "
                f"{sorted(cancelled_sides)}.{fill_note}"
            )
            alerts.append(
                MarSurveillanceAlert(
                    alert_id=f"ALT_SPOOF_{owner}_{instrument}",
                    alert_type="SPOOFING_ALERT",
                    severity=severity,
                    isin=str(group["isin"]),
                    symbol=str(group["symbol"]),
                    details=details,
                    stor_report_required=True,
                    account_id=owner,
                    first_event_timestamp_ns=window_start,
                    last_event_timestamp_ns=window_end,
                    event_ids=event_ids,
                    indicator_reference="Delegated Regulation (EU) 2016/522, Annex II — layering and spoofing",
                    metric_value=cancel_ratio,
                    opposite_side_fill_observed=opposite_fill is not None,
                )
            )
            logger.warning("SPOOFING_ALERT: %s", details)

        return alerts, unmatched_cancels, len(groups)

    def _detect_quote_stuffing(
        self, events: Sequence[OrderExecutionEvent]
    ) -> List[MarSurveillanceAlert]:
        """
        Flag order-book message bursts per (beneficial owner, instrument).

        The peak rate is the largest number of NEW/MODIFY/CANCEL messages falling inside
        any one-second window, measured with a sliding window over sorted timestamps
        rather than fixed calendar buckets, so a burst straddling a bucket boundary is
        still counted in full. Fills are excluded: an execution is not a quote.
        """
        messages: Dict[Tuple[str, str], Dict[str, object]] = {}
        for event in events:
            if event.event_type.strip().upper() not in ORDER_BOOK_EVENT_TYPES:
                continue
            key = (self._order_owner(event), _instrument_key(event))
            bucket = messages.setdefault(key, {"stamps": [], "isin": event.isin, "symbol": event.symbol})
            bucket["stamps"].append((event.timestamp_ns, event.event_id))

        alerts: List[MarSurveillanceAlert] = []
        for (owner, instrument), bucket in sorted(messages.items()):
            stamped: List[Tuple[int, str]] = sorted(bucket["stamps"])
            peak = 0
            peak_window = (0, 0)
            peak_slice: List[Tuple[int, str]] = []
            left = 0
            for right in range(len(stamped)):
                while stamped[right][0] - stamped[left][0] >= NS_PER_SEC:
                    left += 1
                count = right - left + 1
                if count > peak:
                    peak = count
                    peak_window = (stamped[left][0], stamped[right][0])
                    peak_slice = stamped[left:right + 1]

            if peak <= self.quote_rate_threshold_per_sec:
                continue

            details = (
                f"Quote stuffing burst for beneficial owner '{owner}' on {instrument}: {peak} "
                f"order-book messages within a one-second window (threshold "
                f"{self.quote_rate_threshold_per_sec}/s). Check the participant's "
                "market-making obligations before treating this as abusive."
            )
            alerts.append(
                MarSurveillanceAlert(
                    alert_id=f"ALT_QSTUFF_{owner}_{instrument}",
                    alert_type="QUOTE_STUFFING_ALERT",
                    severity="MEDIUM",
                    isin=str(bucket["isin"]),
                    symbol=str(bucket["symbol"]),
                    details=details,
                    stor_report_required=True,
                    account_id=owner,
                    first_event_timestamp_ns=peak_window[0],
                    last_event_timestamp_ns=peak_window[1],
                    event_ids=tuple(event_id for _, event_id in peak_slice[:10]),
                    indicator_reference="Delegated Regulation (EU) 2016/522, Annex II — quote stuffing",
                    metric_value=float(peak),
                )
            )
            logger.warning("QUOTE_STUFFING_ALERT: %s", details)
        return alerts

    def _build_stor_draft(
        self, alerts: Sequence[MarSurveillanceAlert], detection_parameters: Mapping[str, str]
    ) -> Dict[str, str]:
        """
        Assemble the batch-level draft STOR record.

        The record names its legal basis, the template the content must be transposed
        into, the instruments and owners involved, and the parameters that produced the
        alerts (so the analysis is reproducible for the five-year retention obligation),
        and states explicitly that no submission has taken place.
        """
        instruments = sorted({alert.isin or alert.symbol for alert in alerts if alert.isin or alert.symbol})
        owners = sorted({alert.account_id for alert in alerts if alert.account_id})
        return {
            "legal_basis": MAR_LEGAL_BASIS,
            "report_type": "STOR (Suspicious Transaction and Order Report) — DRAFT",
            "template_source": STOR_TEMPLATE_SOURCE,
            "status": STOR_STATUS_DRAFT,
            "submission_channel": (
                "NCA-specific; there is no EU-wide endpoint. Enrol with the competent "
                "authority (e.g. the BaFin MVP portal STOR procedure, or the AMF ROSA "
                "extranet) before any filing."
            ),
            "total_alerts": str(len(alerts)),
            "alert_types": ",".join(sorted({alert.alert_type for alert in alerts})),
            "instruments": ",".join(instruments),
            "beneficial_owners": ",".join(owners),
            "earliest_event_timestamp_ns": str(min(alert.first_event_timestamp_ns for alert in alerts)),
            "latest_event_timestamp_ns": str(max(alert.last_event_timestamp_ns for alert in alerts)),
            "detection_parameters": ";".join(f"{k}={v}" for k, v in sorted(detection_parameters.items())),
            "human_analysis_required": (
                "Yes — Delegated Regulation (EU) 2016/957 requires an appropriate level of "
                "human analysis. An alert is not a determination of reasonable suspicion."
            ),
            "timeliness_obligation": (
                "MAR Article 16: notify without delay once reasonable suspicion is formed. "
                "Do not hold reports back to accumulate cases."
            ),
            "record_retention": STOR_RETENTION_NOTE,
        }
