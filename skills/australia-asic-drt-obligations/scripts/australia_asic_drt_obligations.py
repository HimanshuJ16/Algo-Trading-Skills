"""
australia-asic-drt-obligations:
Validates OTC derivative trade reports against ASIC's Derivative Transaction
Rules (Reporting) 2024, ensuring mandatory ISO identifiers (LEI, UTI, UPI) and
the T+2 (business-day) reporting deadline.

Regulatory basis:
    ASIC Derivative Transaction Rules (Reporting) 2024 (F2022L01706).
        - Rule 2.2.3: a reporting entity must report a Reportable Transaction
          by the end of the second Business Day (T+2) after the day on which
          the transaction occurs, or by the end of the fourth Business Day
          (T+4) where a value is required for Item 92 (linking identifier) of
          Table S1.1(1).
        - Item 2, Table S1.1(1): the UPI must conform to ISO 4914.
        - Rule 2.2.9: the UTI must conform to ISO 23897.
        - Counterparty identification uses the LEI per ISO 17442.

    "Business Day" is measured by reference to Sydney time; weekends are
    excluded. Public holidays are caller-supplied: passing ``holidays=None``
    excludes weekends only and may over-count business days, so production
    deployments should pass the ASIC/Sydney holiday calendar.

Identifier formats:
    LEI (ISO 17442): 20 chars, uppercase alphanumeric, with an ISO/IEC
        7064 MOD 97-10 checksum (numeric representation mod 97 == 1).
    UTI (ISO 23897): 20-52 chars, uppercase alphanumeric (no separators); the
        first 20 characters are the generating entity's ISO 17442 LEI.
    UPI (ISO 4914): 12 chars; a fixed "QZ" prefix followed by 9 base
        characters and 1 check character, each drawn from consonants
        (excluding A, E, I, O, U and Y) plus digits 0-9.
"""
import dataclasses
import logging
import string
from datetime import date, timedelta
from typing import FrozenSet, List, Optional, Set

logger = logging.getLogger(__name__)

# ISO 17442 LEI / ISO 23897 UTI character set: uppercase A-Z and digits 0-9.
_ALPHANUMERIC = frozenset(string.ascii_uppercase + string.digits)
# ISO 4914 UPI character set: consonants (excluding A, E, I, O, U and Y)
# plus digits 0-9.
_UPI_ALPHABET = frozenset(
    (set(string.ascii_uppercase) - set("AEIOUY")) | set(string.digits)
)

_LEI_LENGTH = 20
_UTI_MIN_LENGTH = 20
_UTI_MAX_LENGTH = 52
_UPI_LENGTH = 12
_UPI_PREFIX = "QZ"


def _is_in_alphabet(value: str, alphabet: FrozenSet[str]) -> bool:
    """Return True if ``value`` is non-empty and every character is in
    ``alphabet``."""
    return bool(value) and all(ch in alphabet for ch in value)


def _lei_checksum_valid(lei: str) -> bool:
    """Validate an LEI checksum per ISO/IEC 7064 MOD 97-10.

    Each character is converted to its base-36 value (A=10 ... Z=35) and the
    concatenated digits form an integer that must satisfy
    ``int(digits) % 97 == 1``.
    """
    numeric = "".join(str(int(ch, 36)) for ch in lei)
    return int(numeric) % 97 == 1


def _add_business_days(
    start: date, offset: int, holidays: Optional[Set[date]] = None
) -> date:
    """Return ``start`` advanced by ``offset`` business days, skipping
    weekends and any dates in ``holidays``.

    ``offset`` must be non-negative. A zero offset returns ``start`` unchanged
    even if ``start`` itself falls on a weekend/holiday, mirroring the
    regulatory framing that the clock starts on the trade day.
    """
    if offset < 0:
        raise ValueError("business-day offset must be non-negative")
    holiday_set = holidays or set()
    current = start
    remaining = offset
    while remaining > 0:
        current = current + timedelta(days=1)
        if current.weekday() < 5 and current not in holiday_set:
            remaining -= 1
    return current


@dataclasses.dataclass
class OtcDerivativeTrade:
    internal_trade_id: str
    trade_date: date
    # Mandatory ASIC 2024 fields.
    lei_counterparty: Optional[str] = None  # ISO 17442
    uti: Optional[str] = None               # ISO 23897
    upi: Optional[str] = None               # ISO 4914
    # When True, the report requires an Item 92 linking identifier and the
    # reporting deadline is extended from T+2 to T+4 business days
    # (ASIC DRT 2024, Rule 2.2.3). Defaults to False (T+2).
    requires_linking_identifier: bool = False


@dataclasses.dataclass
class DrtComplianceRecord:
    internal_trade_id: str
    is_ready_for_reporting: bool
    is_late_submission: bool
    missing_fields: List[str]
    reporting_deadline: date


class AsicDrtReportingEngine:
    """Validates OTC derivative trades for ASIC Trade Repository reporting
    compliance under the ASIC Derivative Transaction Rules (Reporting) 2024.
    """

    def validate_report(
        self,
        trade: OtcDerivativeTrade,
        reporting_date: date,
        holidays: Optional[Set[date]] = None,
    ) -> DrtComplianceRecord:
        """Validate a single trade and return its compliance record.

        ``holidays`` is an optional set of dates treated as non-business days
        (public holidays). When omitted, only weekends are skipped.
        """
        missing_fields: List[str] = []
        is_ready = True

        # 1. LEI (ISO 17442): 20 uppercase alphanumeric chars + MOD 97-10 checksum.
        lei = (trade.lei_counterparty or "").strip()
        if (
            len(lei) != _LEI_LENGTH
            or not _is_in_alphabet(lei, _ALPHANUMERIC)
            or not _lei_checksum_valid(lei)
        ):
            missing_fields.append("LEI (ISO 17442) is missing or invalid.")
            is_ready = False

        # 2. UTI (ISO 23897): 20-52 uppercase alphanumeric chars (no separators).
        uti = (trade.uti or "").strip()
        if (
            not (_UTI_MIN_LENGTH <= len(uti) <= _UTI_MAX_LENGTH)
            or not _is_in_alphabet(uti, _ALPHANUMERIC)
        ):
            missing_fields.append("UTI (ISO 23897) is missing or invalid.")
            is_ready = False

        # 3. UPI (ISO 4914): 12 chars, "QZ" prefix, consonant+digit alphabet.
        upi = (trade.upi or "").strip()
        if (
            len(upi) != _UPI_LENGTH
            or not upi.startswith(_UPI_PREFIX)
            or not _is_in_alphabet(upi, _UPI_ALPHABET)
        ):
            missing_fields.append("UPI (ISO 4914) is missing or invalid.")
            is_ready = False

        # 4. Reporting deadline (ASIC DRT 2024, Rule 2.2.3): T+2 business days,
        #    extended to T+4 business days where a linking identifier is required.
        offset = 4 if trade.requires_linking_identifier else 2
        deadline = _add_business_days(trade.trade_date, offset, holidays)
        is_late = reporting_date > deadline

        if is_late:
            logger.warning(
                "Trade %s breaches the ASIC T+%d reporting deadline. "
                "Trade date: %s, reporting date: %s, deadline: %s",
                trade.internal_trade_id, offset,
                trade.trade_date, reporting_date, deadline,
            )
        if not is_ready:
            logger.error(
                "Trade %s failed ASIC DRT validation. Missing: %s",
                trade.internal_trade_id, missing_fields,
            )
        else:
            logger.info("Trade %s passed ASIC DRT validation.", trade.internal_trade_id)

        return DrtComplianceRecord(
            internal_trade_id=trade.internal_trade_id,
            is_ready_for_reporting=is_ready,
            is_late_submission=is_late,
            missing_fields=missing_fields,
            reporting_deadline=deadline,
        )

    def batch_validate(
        self,
        trades: List[OtcDerivativeTrade],
        reporting_date: date,
        holidays: Optional[Set[date]] = None,
    ) -> List[DrtComplianceRecord]:
        """Validate a batch of trades against a common reporting date."""
        return [
            self.validate_report(trade, reporting_date, holidays)
            for trade in trades
        ]
