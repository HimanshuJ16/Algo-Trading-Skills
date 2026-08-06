import logging
import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class Rule611Status(Enum):
    COMPLIANT_NO_TRADE_THROUGH = "COMPLIANT_NO_TRADE_THROUGH"
    TRADE_THROUGH_VIOLATION = "TRADE_THROUGH_VIOLATION" # Executed at price inferior to protected NBBO
    EXEMPT_ISO = "EXEMPT_ISO"                           # Intermarket Sweep Order exemption (Rule 611(b)(5/6))
    EXEMPT_SELF_HELP = "EXEMPT_SELF_HELP"               # Self-Help venue outage exemption (Rule 611(b)(1))
    EXEMPT_BENCHMARK = "EXEMPT_BENCHMARK"               # VWAP / Benchmark order exemption (Rule 611(b)(7))
    EXEMPT_FLICKERING_QUOTE = "EXEMPT_FLICKERING_QUOTE"# Quote updated within 1 second (Rule 611(b)(8))


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class RegNMSError(Exception):
    """Base exception for SEC Reg NMS compliance processing errors."""
    pass


@dataclass
class ProtectedQuote:
    venue_id: str
    symbol: str
    nbb_price: float        # Protected National Best Bid
    nbb_size: int
    nbo_price: float        # Protected National Best Offer
    nbo_size: int
    is_automated: bool = True # Rule 611 only protects automated quotes
    timestamp: datetime.datetime = field(default_factory=datetime.datetime.utcnow)


@dataclass
class ExecutionRecord:
    execution_id: str
    symbol: str
    side: OrderSide
    price: float
    quantity: int
    venue_id: str
    execution_timestamp: datetime.datetime
    is_iso_tagged: bool = False
    is_benchmark_vwap: bool = False


@dataclass
class Rule611AuditResult:
    execution_id: str
    is_compliant: bool
    status: Rule611Status
    trade_through_bps: float
    executed_price: float
    protected_nbb: float
    protected_nbo: float
    violating_venue_id: Optional[str]
    exemption_reason: str
    audit_timestamp: datetime.datetime = field(default_factory=datetime.datetime.utcnow)


class RegNMSOrderProtectionEngine:
    """
    Institutional SEC Regulation NMS Rule 611 (Order Protection Rule) Engine.
    
    Verifies trade executions against Protected National Best Bid and Offer (NBBO),
    detects illegal trade-throughs, validates statutory Rule 611 exemptions (ISO, Self-Help,
    Benchmark/VWAP, Flickering Quotes), and generates audit trails for SEC / FINRA examinations.
    """

    def __init__(self):
        self.self_help_declarations: Dict[str, datetime.datetime] = {} # venue_id -> declared_at
        logger.info("Initialized SEC Regulation NMS Rule 611 Order Protection Engine")

    def declare_self_help(self, venue_id: str, reason: str = "System Latency / Outage > 1 sec") -> None:
        """
        Declares Self-Help exemption (Rule 611(b)(1)) against a malfunctioning trading venue.
        Ignores quotes displayed by the specified venue during trade-through evaluations.
        """
        self.self_help_declarations[venue_id] = datetime.datetime.utcnow()
        logger.warning(f"SEC Reg NMS Self-Help DECLARED against venue '{venue_id}': {reason}")

    def revoke_self_help(self, venue_id: str) -> None:
        """Revokes active Self-Help declaration once venue performance restores."""
        if venue_id in self.self_help_declarations:
            del self.self_help_declarations[venue_id]
            logger.info(f"SEC Reg NMS Self-Help REVOKED for venue '{venue_id}'")

    def is_self_help_active(self, venue_id: str) -> bool:
        """Checks whether Self-Help is active for a given venue."""
        return venue_id in self.self_help_declarations

    def compute_protected_nbbo(
        self, quotes: List[ProtectedQuote]
    ) -> Tuple[float, float, Optional[str], Optional[str]]:
        """
        Computes National Best Bid (NBB) and Best Offer (NBO) strictly from automated,
        non-Self-Help protected venue quotes.
        """
        valid_bids = []
        valid_offers = []

        for q in quotes:
            # Rule 611 only protects automated quotes from venues not under Self-Help
            if q.is_automated and not self.is_self_help_active(q.venue_id):
                if q.nbb_price > 0:
                    valid_bids.append((q.nbb_price, q.venue_id))
                if q.nbo_price > 0:
                    valid_offers.append((q.nbo_price, q.venue_id))

        if not valid_bids or not valid_offers:
            raise RegNMSError("No valid protected automated quotes available to compute NBBO.")

        best_nbb, nbb_venue = max(valid_bids, key=lambda x: x[0])
        best_nbo, nbo_venue = min(valid_offers, key=lambda x: x[0])

        return best_nbb, best_nbo, nbb_venue, nbo_venue

    def evaluate_execution(
        self, exec_rec: ExecutionRecord, protected_quotes: List[ProtectedQuote]
    ) -> Rule611AuditResult:
        """
        Evaluates a single execution for SEC Rule 611 trade-through compliance or statutory exemptions.
        """
        # 1. Statutory Exemption Check: Intermarket Sweep Order (ISO)
        if exec_rec.is_iso_tagged:
            logger.info(f"Rule 611 Audit [{exec_rec.execution_id}]: EXEMPT (Tagged as Intermarket Sweep Order ISO)")
            return Rule611AuditResult(
                execution_id=exec_rec.execution_id,
                is_compliant=True,
                status=Rule611Status.EXEMPT_ISO,
                trade_through_bps=0.0,
                executed_price=exec_rec.price,
                protected_nbb=0.0,
                protected_nbo=0.0,
                violating_venue_id=None,
                exemption_reason="Intermarket Sweep Order (ISO) Rule 611(b)(5/6) Exemption",
            )

        # 2. Statutory Exemption Check: Benchmark / VWAP Order
        if exec_rec.is_benchmark_vwap:
            logger.info(f"Rule 611 Audit [{exec_rec.execution_id}]: EXEMPT (Benchmark / VWAP Execution)")
            return Rule611AuditResult(
                execution_id=exec_rec.execution_id,
                is_compliant=True,
                status=Rule611Status.EXEMPT_BENCHMARK,
                trade_through_bps=0.0,
                executed_price=exec_rec.price,
                protected_nbb=0.0,
                protected_nbo=0.0,
                violating_venue_id=None,
                exemption_reason="Benchmark / VWAP Rule 611(b)(7) Exemption",
            )

        # Compute Protected NBBO
        nbb, nbo, nbb_venue, nbo_venue = self.compute_protected_nbbo(protected_quotes)

        # 3. Trade-Through Detection
        is_trade_through = False
        trade_through_bps = 0.0
        violating_venue = None

        if exec_rec.side == OrderSide.BUY:
            # Buying above National Best Offer (NBO) is a trade-through
            if exec_rec.price > nbo + 1e-6:
                is_trade_through = True
                trade_through_bps = ((exec_rec.price - nbo) / nbo) * 10_000.0
                violating_venue = nbo_venue
        else:  # OrderSide.SELL
            # Selling below National Best Bid (NBB) is a trade-through
            if exec_rec.price < nbb - 1e-6:
                is_trade_through = True
                trade_through_bps = ((nbb - exec_rec.price) / nbb) * 10_000.0
                violating_venue = nbb_venue

        # 4. Check Flickering Quotes Exemption (Rule 611(b)(8)) - Quote updated within 1 second
        if is_trade_through:
            # Check if any protected quote was updated within 1.0 second of execution
            recent_quote_update = any(
                abs((exec_rec.execution_timestamp - q.timestamp).total_seconds()) <= 1.0
                for q in protected_quotes
            )
            if recent_quote_update:
                logger.info(f"Rule 611 Audit [{exec_rec.execution_id}]: EXEMPT (Flickering Quote within 1s)")
                return Rule611AuditResult(
                    execution_id=exec_rec.execution_id,
                    is_compliant=True,
                    status=Rule611Status.EXEMPT_FLICKERING_QUOTE,
                    trade_through_bps=trade_through_bps,
                    executed_price=exec_rec.price,
                    protected_nbb=nbb,
                    protected_nbo=nbo,
                    violating_venue_id=violating_venue,
                    exemption_reason="Flickering Quote Rule 611(b)(8) Exemption (quote updated within 1s)",
                )

            logger.error(
                f"Rule 611 TRADE-THROUGH VIOLATION [{exec_rec.execution_id}]: Side={exec_rec.side.value}, "
                f"Exec Price=${exec_rec.price:.4f}, Protected NBB=${nbb:.4f}, NBO=${nbo:.4f} (Bps={trade_through_bps:.2f})"
            )
            return Rule611AuditResult(
                execution_id=exec_rec.execution_id,
                is_compliant=False,
                status=Rule611Status.TRADE_THROUGH_VIOLATION,
                trade_through_bps=trade_through_bps,
                executed_price=exec_rec.price,
                protected_nbb=nbb,
                protected_nbo=nbo,
                violating_venue_id=violating_venue,
                exemption_reason="No Exemption Applies - Illegal Trade-Through Executed",
            )

        logger.info(f"Rule 611 Audit [{exec_rec.execution_id}]: COMPLIANT (No Trade-Through)")
        return Rule611AuditResult(
            execution_id=exec_rec.execution_id,
            is_compliant=True,
            status=Rule611Status.COMPLIANT_NO_TRADE_THROUGH,
            trade_through_bps=0.0,
            executed_price=exec_rec.price,
            protected_nbb=nbb,
            protected_nbo=nbo,
            violating_venue_id=None,
            exemption_reason="Execution at or better than Protected NBBO",
        )

