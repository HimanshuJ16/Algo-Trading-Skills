"""
best-execution-record-keeping-global: execution-quality screening and tamper-evident
audit logging for trade records.

What this module is
-------------------
A *screening and evidence-capture* tool. It compares an order's volume-weighted fill
price against a caller-supplied benchmark, checks that the metadata a firm has decided
it must retain is actually present, and appends every record to a hash-chained audit
log so later modification is detectable.

What this module is NOT
-----------------------
**`is_compliant=True` is not a determination that best execution was achieved.** Best
execution under MiFID II Article 27 and FINRA Rule 5310 is a multi-factor, process-based
obligation weighing price, costs, speed, likelihood of execution and settlement, size,
nature, and any other relevant consideration. A single slippage-versus-benchmark
threshold captures one factor. Treat a pass as "nothing flagged by this screen", never
as evidence of compliance. See `references/standards.md`.

The default `slippage_tolerance` is a **firm-chosen risk parameter with no regulatory
basis** — no regulator prescribes a numeric slippage threshold. Calibrate it per
instrument, venue, and order type, and record the rationale.

Tamper-evidence, precisely
--------------------------
Each audit entry carries the SHA-256 of the trade record, the previous entry's hash,
and its own hash over both. Editing any entry breaks every subsequent link, which
`verify_audit_log()` detects. This makes modification *detectable by anyone holding a
later hash* — it does not make records immutable. Anyone able to rewrite the whole log
can recompute the entire chain. Immutability is a storage property: publish or escrow
the head hash externally, and keep the log on a retention-locked store. Under SEC Rule
17a-4 (as amended 12 October 2022, effective 3 January 2023) WORM is one permitted
option and an audit-trail alternative is another; this module supports the evidence
side of either, and substitutes for neither.
"""
import hashlib
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Timestamp keys the engine requires by default. Mirrors the sequence in
# references/workflows.md; override per the record set your obligation actually covers.
DEFAULT_REQUIRED_TIMESTAMPS: Tuple[str, ...] = ("creation", "submission", "execution")

# Genesis link for the hash chain -- the "previous hash" of the first entry.
_GENESIS_HASH = "0" * 64


class TimestampPrecision(Enum):
    """
    Required precision of the *recorded timestamp string*.

    The values mirror the granularity rows of MiFID II RTS 25 (Commission Delegated
    Regulation (EU) 2017/574), which sets granularity by activity: 1 second for voice
    trading, request-for-quote with human intervention, and negotiated transactions;
    1 millisecond for other trading activity; 1 microsecond for high-frequency
    algorithmic trading. There is no single universal figure and RTS 25 nowhere
    requires nanoseconds -- pick the row matching your activity.
    """

    SECOND = 0
    MILLISECOND = 3
    MICROSECOND = 6


class BestExRecordError(ValueError):
    """Raised only when a record cannot be processed or hashed at all."""


@dataclass
class TradeRecord:
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: Optional[float]
    order_type: str
    venue: str
    algo_id: str
    trader_id: str
    client_id: str
    timestamps: Dict[str, str]  # e.g. 'creation', 'submission', 'execution' (ISO-8601 UTC)
    fills: List[Dict[str, float]]  # e.g. [{'price': 100.5, 'qty': 10, 'timestamp': '...'}]
    benchmark_price: Optional[float] = None
    regulatory_tags: Dict[str, str] = field(default_factory=dict)

    def calculate_average_fill_price(self) -> float:
        """
        Volume-weighted average fill price, or 0.0 when there are no fills.

        :raises BestExRecordError: if a fill is missing 'price'/'qty' or they are not
            finite numbers. Previously a malformed fill raised a bare `KeyError` from
            inside the sum, which gave the caller no indication of which record failed.
        """
        if not self.fills:
            return 0.0

        total_value = 0.0
        total_qty = 0.0
        for index, fill in enumerate(self.fills):
            price = _require_finite_number(fill, "price", index, self.order_id)
            qty = _require_finite_number(fill, "qty", index, self.order_id)
            total_value += price * qty
            total_qty += qty

        return total_value / total_qty if total_qty > 0 else 0.0


def _require_finite_number(fill: Dict[str, Any], key: str, index: int, order_id: str) -> float:
    """Extract a finite numeric field from a fill dict, or raise."""
    if key not in fill:
        raise BestExRecordError(f"Order {order_id!r}: fill[{index}] is missing {key!r}.")
    value = fill[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BestExRecordError(
            f"Order {order_id!r}: fill[{index}][{key!r}] must be numeric, got {value!r}."
        )
    numeric = float(value)
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        raise BestExRecordError(
            f"Order {order_id!r}: fill[{index}][{key!r}] must be finite, got {value!r}."
        )
    return numeric


@dataclass
class BestExAnalysis:
    """
    Outcome of the screen for one record.

    :param is_compliant: True only when *no* check flagged. Not a best-execution
        determination -- see the module docstring.
    :param slippage: Adverse-cost convention: positive means the fill was worse than
        the benchmark, for both buys and sells. `nan` when slippage could not be
        evaluated; read `slippage_evaluated` rather than testing the number, since a
        previous version reported an un-evaluated slippage as `0.0`, which is
        indistinguishable from a perfect fill.
    :param slippage_evaluated: Whether a slippage figure was actually computed.
    :param violations: Every check that flagged, not just the last one. `notes` is a
        human-readable rendering of this list.
    :param record_hash: SHA-256 over the trade record. Always populated, including for
        records that failed validation.
    """

    order_id: str
    is_compliant: bool
    slippage: float
    notes: str
    record_hash: str
    violations: List[str] = field(default_factory=list)
    slippage_evaluated: bool = False


class BestExecutionRecordKeepingGlobalEngine:
    """
    Screens trade records for execution-quality outliers and missing regulatory
    metadata, and appends every screened record to a hash-chained audit log.

    Jurisdiction-neutral by design: which metadata is mandatory, which retention period
    applies, and which timestamp granularity is required all depend on the firm's
    regulatory status and jurisdiction. Supply them via the constructor rather than
    relying on defaults. See `references/standards.md` for the per-regime detail.
    """

    def __init__(
        self,
        slippage_tolerance: float = 0.05,
        required_timestamps: Sequence[str] = DEFAULT_REQUIRED_TIMESTAMPS,
        required_tags: Sequence[str] = (),
        timestamp_precision: Optional[TimestampPrecision] = None,
    ):
        """
        :param slippage_tolerance: Adverse slippage above which a record is flagged,
            as a decimal (0.05 = 5%). **A firm-chosen risk parameter, not a regulatory
            threshold** -- no regulator prescribes a number here.
        :param required_timestamps: Timestamp keys that must be present, parseable,
            UTC, and non-decreasing in this order.
        :param required_tags: Specific `regulatory_tags` keys that must be present.
            Empty means "any non-empty tag set passes", which is a weak check -- name
            the tags your regime actually requires (for example an LEI, which MiFIR
            Article 26 transaction reporting requires for the entities involved).
        :param timestamp_precision: If set, timestamp strings must carry at least this
            many fractional-second digits. See `TimestampPrecision` for the RTS 25
            activity mapping. Default `None` performs no precision check.
        :raises BestExRecordError: on a non-finite or negative slippage tolerance.
        """
        if not isinstance(slippage_tolerance, (int, float)) or isinstance(slippage_tolerance, bool):
            raise BestExRecordError(
                f"slippage_tolerance must be numeric, got {slippage_tolerance!r}."
            )
        tolerance = float(slippage_tolerance)
        if tolerance != tolerance or tolerance in (float("inf"), float("-inf")):
            raise BestExRecordError(f"slippage_tolerance must be finite, got {slippage_tolerance!r}.")
        if tolerance < 0:
            raise BestExRecordError(
                f"slippage_tolerance must be non-negative, got {tolerance!r}. A negative "
                "tolerance flags every execution, including perfect ones."
            )

        self.slippage_tolerance = tolerance
        self.required_timestamps = tuple(required_timestamps)
        self.required_tags = tuple(required_tags)
        self.timestamp_precision = timestamp_precision
        self.audit_log: List[Dict[str, Any]] = []

    # ---------------------------------------------------------------- hashing

    def _generate_record_hash(self, record: TradeRecord) -> str:
        """
        SHA-256 over the record's canonical JSON form.

        `default=str` stringifies values JSON cannot represent natively (a `datetime` or
        `Decimal` left in a fill, say). Without it a single such value raises `TypeError`
        here and the record never reaches the audit log — the exact failure this engine
        exists to prevent. Stringification is deterministic, so hashes stay stable, but
        prefer JSON-native types so the hashed form matches the exported form exactly.
        """
        record_json = json.dumps(
            asdict(record), sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(record_json.encode("utf-8")).hexdigest()

    @staticmethod
    def _generate_entry_hash(previous_hash: str, record_hash: str, analysis: Dict[str, Any]) -> str:
        """
        Chain link: hash of (previous entry hash, record hash, analysis).

        Including `previous_hash` is what makes the log tamper-evident. Hashing each
        record in isolation -- the previous behaviour -- lets anyone who edits a record
        recompute its hash and leave no trace.
        """
        payload = json.dumps(
            {"previous": previous_hash, "record": record_hash, "analysis": analysis},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def head_hash(self) -> str:
        """
        Hash of the most recent entry, or the genesis value when the log is empty.

        Publish or escrow this externally (and periodically) to get real tamper
        *evidence*: without an external anchor, a party who can rewrite the whole log
        can recompute a self-consistent chain.
        """
        return self.audit_log[-1]["entry_hash"] if self.audit_log else _GENESIS_HASH

    # ------------------------------------------------------------ validation

    def _check_fills(self, record: TradeRecord) -> Tuple[Optional[float], List[str]]:
        """Return (average fill price, violations). Average is None when unusable."""
        if not isinstance(record.fills, list):
            return None, [
                f"fills must be a list of fill dicts, got {type(record.fills).__name__}."
            ]
        if not record.fills:
            return None, ["No fills recorded; execution quality cannot be assessed."]

        try:
            avg_price = record.calculate_average_fill_price()
        except BestExRecordError as exc:
            return None, [f"Malformed fill data: {exc}"]

        violations: List[str] = []
        total_qty = sum(float(f["qty"]) for f in record.fills)
        if total_qty <= 0:
            violations.append(f"Total filled quantity is {total_qty!r}; expected a positive quantity.")
            return None, violations
        if any(float(f["qty"]) < 0 for f in record.fills):
            violations.append("At least one fill has a negative quantity.")
        if any(float(f["price"]) < 0 for f in record.fills):
            violations.append("At least one fill has a negative price.")
        if avg_price <= 0:
            violations.append(f"Average fill price is {avg_price!r}; expected a positive price.")
            return None, violations

        return avg_price, violations

    def _check_timestamps(self, record: TradeRecord) -> List[str]:
        """Presence, parseability, UTC, ordering, and optional precision."""
        if not isinstance(record.timestamps, dict):
            return [
                f"timestamps must be a dict of name -> ISO-8601 string, got "
                f"{type(record.timestamps).__name__}."
            ]

        violations: List[str] = []
        parsed: Dict[str, datetime] = {}

        for key in self.required_timestamps:
            raw = record.timestamps.get(key)
            if not raw:
                violations.append(f"Missing {key!r} timestamp.")
                continue

            moment = _parse_iso8601(raw)
            if moment is None:
                violations.append(f"Timestamp {key!r} is not parseable ISO-8601: {raw!r}.")
                continue
            if moment.tzinfo is None:
                violations.append(
                    f"Timestamp {key!r} has no timezone offset: {raw!r}. RTS 25 requires "
                    "business clocks to be traceable to UTC; record an explicit offset."
                )
                continue
            if moment.utcoffset() != timedelta(0):
                violations.append(f"Timestamp {key!r} is not UTC: {raw!r}.")
                continue

            parsed[key] = moment

            if self.timestamp_precision is not None:
                digits = _fractional_digits(raw)
                required = self.timestamp_precision.value
                if digits < required:
                    violations.append(
                        f"Timestamp {key!r} records {digits} fractional-second digit(s); "
                        f"{self.timestamp_precision.name.lower()} granularity needs {required}."
                    )

        ordered = [k for k in self.required_timestamps if k in parsed]
        for earlier, later in zip(ordered, ordered[1:]):
            if parsed[earlier] > parsed[later]:
                violations.append(
                    f"Timestamp {later!r} ({record.timestamps[later]}) precedes "
                    f"{earlier!r} ({record.timestamps[earlier]})."
                )

        return violations

    def _check_tags(self, record: TradeRecord) -> List[str]:
        """Regulatory metadata presence. A whitespace-only value counts as missing."""
        if not isinstance(record.regulatory_tags, dict) or not record.regulatory_tags:
            return ["Missing regulatory tags entirely."]
        missing = sorted(
            tag
            for tag in self.required_tags
            if not str(record.regulatory_tags.get(tag) or "").strip()
        )
        return [f"Missing required regulatory tag(s): {missing}."] if missing else []

    def _check_slippage(
        self, record: TradeRecord, avg_price: Optional[float]
    ) -> Tuple[float, bool, List[str]]:
        """
        Return (slippage, evaluated, violations).

        Adverse-cost convention: positive slippage means the execution was worse than
        the benchmark, for buys and sells alike.
        """
        if avg_price is None:
            return float("nan"), False, []

        benchmark = record.benchmark_price
        if benchmark is None:
            return float("nan"), False, [
                "No benchmark price supplied; execution quality was not assessed. A "
                "record with no benchmark is not evidence of best execution."
            ]
        if not isinstance(benchmark, (int, float)) or isinstance(benchmark, bool):
            return float("nan"), False, [f"Benchmark price is not numeric: {benchmark!r}."]
        benchmark = float(benchmark)
        if benchmark != benchmark or benchmark in (float("inf"), float("-inf")):
            return float("nan"), False, [f"Benchmark price is not finite: {benchmark!r}."]
        if benchmark <= 0:
            return float("nan"), False, [
                f"Benchmark price must be positive, got {benchmark!r}; slippage is undefined."
            ]

        if not isinstance(record.side, str):
            return float("nan"), False, [
                f"side must be a string, got {type(record.side).__name__}; "
                "cannot orient the slippage sign."
            ]

        side = record.side.upper().strip()
        if side in ("BUY", "B", "BOT"):
            slippage = (avg_price - benchmark) / benchmark
        elif side in ("SELL", "S", "SLD"):
            slippage = (benchmark - avg_price) / benchmark
        else:
            return float("nan"), False, [
                f"Unrecognised side {record.side!r}; cannot orient the slippage sign."
            ]

        violations: List[str] = []
        if slippage > self.slippage_tolerance:
            # Basis-point resolution: at 2dp a slippage of 0.001523 against a 0.0015
            # tolerance renders as "0.15% exceeded 0.15%", which reads as a
            # contradiction in a compliance record an examiner may later read.
            violations.append(
                f"Slippage {slippage:.4%} exceeded tolerance {self.slippage_tolerance:.4%}."
            )
        return slippage, True, violations

    # --------------------------------------------------------------- screening

    def run_best_ex_checks(self, record: TradeRecord) -> BestExAnalysis:
        """
        Screen one record and append it to the audit log.

        Every record reaches the audit log, including one that fails validation — a
        record that cannot be assessed is exactly the record an examiner will ask
        about, and the previous version returned early on a missing execution timestamp
        without logging anything or producing a hash.

        :return: A `BestExAnalysis` listing *all* violations found, not only the last.
        """
        if not isinstance(record, TradeRecord):
            raise BestExRecordError(f"Expected a TradeRecord, got {type(record).__name__}.")

        avg_price, violations = self._check_fills(record)
        violations = list(violations)
        violations.extend(self._check_timestamps(record))
        violations.extend(self._check_tags(record))

        slippage, evaluated, slippage_violations = self._check_slippage(record, avg_price)
        violations.extend(slippage_violations)

        is_compliant = not violations
        notes = (
            "No exceptions raised by this screen. Not a best-execution determination."
            if is_compliant
            else " ".join(violations)
        )

        analysis = BestExAnalysis(
            order_id=record.order_id,
            is_compliant=is_compliant,
            slippage=slippage,
            notes=notes,
            record_hash=self._generate_record_hash(record),
            violations=violations,
            slippage_evaluated=evaluated,
        )

        self._append_audit_entry(record, analysis)

        if is_compliant:
            logger.info("Order %s screened clean (slippage %s).", record.order_id, _fmt(slippage))
        else:
            logger.warning(
                "Order %s flagged %d exception(s): %s",
                record.order_id,
                len(violations),
                " ".join(violations),
            )

        return analysis

    def _append_audit_entry(self, record: TradeRecord, analysis: BestExAnalysis) -> None:
        """Append a chained, tamper-evident entry."""
        analysis_dict = asdict(analysis)
        previous_hash = self.head_hash
        entry = {
            "sequence": len(self.audit_log),
            "record": asdict(record),
            "analysis": analysis_dict,
            "previous_entry_hash": previous_hash,
            "entry_hash": self._generate_entry_hash(
                previous_hash, analysis.record_hash, analysis_dict
            ),
        }
        self.audit_log.append(entry)

    # ------------------------------------------------------------ verification

    def verify_audit_log(self) -> List[str]:
        """
        Recompute the chain and report every inconsistency found.

        :return: An empty list when the log is internally consistent; otherwise one
            message per problem, identifying the entry by sequence number.

        An empty result proves internal consistency only. It cannot detect a log that
        was rewritten wholesale — compare `head_hash` against an externally anchored
        value for that.
        """
        problems: List[str] = []
        expected_previous = _GENESIS_HASH

        for index, entry in enumerate(self.audit_log):
            if entry.get("sequence") != index:
                problems.append(
                    f"Entry {index}: sequence is {entry.get('sequence')!r}, expected {index} "
                    "(entries inserted, removed, or reordered)."
                )
            if entry.get("previous_entry_hash") != expected_previous:
                problems.append(
                    f"Entry {index}: previous_entry_hash does not match the preceding entry; "
                    "the chain is broken here."
                )

            try:
                record = TradeRecord(**entry["record"])
            except (TypeError, KeyError) as exc:
                problems.append(f"Entry {index}: stored record is not a valid TradeRecord ({exc}).")
                expected_previous = entry.get("entry_hash", "")
                continue

            recomputed_record_hash = self._generate_record_hash(record)
            stored_record_hash = entry.get("analysis", {}).get("record_hash")
            if recomputed_record_hash != stored_record_hash:
                problems.append(
                    f"Entry {index}: the stored record does not match its recorded hash "
                    "(the record was modified after screening)."
                )

            recomputed_entry_hash = self._generate_entry_hash(
                entry.get("previous_entry_hash", ""),
                stored_record_hash or "",
                entry.get("analysis", {}),
            )
            if recomputed_entry_hash != entry.get("entry_hash"):
                problems.append(
                    f"Entry {index}: entry_hash does not match its contents "
                    "(the entry was modified after screening)."
                )

            expected_previous = entry.get("entry_hash", "")

        if problems:
            logger.error("Audit log verification failed with %d problem(s).", len(problems))
        return problems

    # ----------------------------------------------------------------- export

    def export_audit_log(self, file_path: str) -> None:
        """
        Write the audit log as UTF-8 JSON.

        Explicit UTF-8 matters: without it Python uses the platform default encoding,
        which on Windows is a legacy codepage that mangles or refuses non-ASCII
        instrument names and counterparty identifiers.

        Writing a file is not archiving. Retention obligations differ by regime — see
        `references/standards.md` — and a plain file on a writable disk satisfies none
        of them on its own.
        """
        with open(file_path, "w", encoding="utf-8") as handle:
            json.dump(self.audit_log, handle, indent=4, ensure_ascii=False)
        logger.info("Exported %d audit entries to %s", len(self.audit_log), file_path)


def _parse_iso8601(value: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp, tolerating a trailing 'Z'. None if unparseable."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if candidate.endswith(("Z", "z")):
        # datetime.fromisoformat only accepts 'Z' from Python 3.11 onward.
        candidate = candidate[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        return None


def _fractional_digits(value: str) -> int:
    """
    Count fractional-second digits in an ISO-8601 string.

    This measures the precision the record actually *carries*, not clock accuracy: a
    string with no fractional part cannot evidence millisecond granularity whatever the
    underlying clock did. Clock accuracy and traceability to UTC are infrastructure
    matters under RTS 25 Article 4 that no downstream string inspection can confirm.
    """
    candidate = value.strip()
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1]
    for offset_marker in ("+", "-"):
        marker_index = candidate.rfind(offset_marker)
        if marker_index > 10:  # past the date portion
            candidate = candidate[:marker_index]
            break
    if "." not in candidate:
        return 0
    return len(candidate.rsplit(".", 1)[1])


def _fmt(value: float) -> str:
    """Format a possibly-nan slippage for logging."""
    return "not evaluated" if value != value else f"{value:.2%}"
