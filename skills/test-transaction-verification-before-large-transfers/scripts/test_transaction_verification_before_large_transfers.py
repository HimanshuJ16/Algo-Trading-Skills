import logging
import uuid
import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Set
from enum import Enum

logger = logging.getLogger(__name__)


class VerificationStatus(Enum):
    NOT_REQUIRED = "NOT_REQUIRED"  # Transfer below large threshold
    TEST_PENDING = "TEST_PENDING"    # Test transaction initiated, awaiting confirmation
    TEST_CONFIRMED = "TEST_CONFIRMED"# Test transaction verified on-chain
    APPROVED = "APPROVED"            # Primary large transfer authorized for execution
    EXPIRED = "EXPIRED"              # Test verification window elapsed
    REJECTED = "REJECTED"            # Failed verification or policy violation


class RiskLevel(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class VerificationError(Exception):
    """Base exception for transfer verification errors."""
    pass


class WhitelistError(VerificationError):
    """Raised when recipient address is not on approved whitelist."""
    pass


class TestTransactionExpiredError(VerificationError):
    """Raised when the time between test transaction and primary transfer exceeds limit."""
    pass


class TestTransactionPendingError(VerificationError):
    """Raised when primary transfer is attempted before test tx confirmation."""
    pass


@dataclass
class AssetConfig:
    symbol: str  # e.g., 'ETH', 'BTC', 'USDT'
    chain: str  # e.g., 'ETHEREUM', 'BITCOIN'
    decimals: int = 18
    min_confirmations: int = 12
    test_amount: float = 0.001  # Small dust amount for test
    requires_destination_tag: bool = False


@dataclass
class VerificationConfig:
    large_transfer_threshold_usd: float = 50_000.0
    test_expiry_window_minutes: int = 30
    enforce_whitelisting: bool = True
    allow_bypass_for_whitelisted: bool = False  # Strict policy: test required even if whitelisted


@dataclass
class TransferRequest:
    request_id: str
    asset_symbol: str
    sender_address: str
    recipient_address: str
    amount: float
    value_usd: float
    destination_tag: Optional[str] = None
    timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


@dataclass
class TestTransaction:
    request_id: str
    tx_hash: str
    amount_sent: float
    confirmations: int = 0
    status: VerificationStatus = VerificationStatus.TEST_PENDING
    initiated_at: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    confirmed_at: Optional[datetime.datetime] = None


@dataclass
class VerificationResult:
    request_id: str
    is_approved: bool
    status: VerificationStatus
    risk_level: RiskLevel
    message: str
    test_tx_hash: Optional[str] = None
    audit_trail: List[str] = field(default_factory=list)


class TransferVerificationEngine:
    """
    Institutional Transfer Verification Engine for Crypto Custody & Treasury.
    
    Enforces dust test transactions prior to authorizing high-value asset movements,
    verifying on-chain confirmation depth, recipient address whitelisting,
    destination tag requirements, and time-decay authorization windows.
    """

    def __init__(self, config: VerificationConfig):
        self.config = config
        self.asset_configs: Dict[str, AssetConfig] = {}
        self.whitelist: Set[str] = set()
        self.pending_requests: Dict[str, TransferRequest] = {}
        self.test_transactions: Dict[str, TestTransaction] = {}
        logger.info(
            f"Initialized Transfer Verification Engine (Threshold: ${config.large_transfer_threshold_usd:,.2f} USD)"
        )

    def register_asset(self, asset_cfg: AssetConfig) -> None:
        """Register asset security parameters and confirmation requirements."""
        self.asset_configs[asset_cfg.symbol] = asset_cfg
        logger.info(
            f"Registered Asset: {asset_cfg.symbol} on {asset_cfg.chain} "
            f"(Min Confirmations: {asset_cfg.min_confirmations}, Test Amount: {asset_cfg.test_amount})"
        )

    def add_to_whitelist(self, address: str) -> None:
        """Add recipient address to internal approved whitelist."""
        norm_addr = address.lower().strip()
        self.whitelist.add(norm_addr)
        logger.info(f"Address added to whitelist: {norm_addr}")

    def is_whitelisted(self, address: str) -> bool:
        return address.lower().strip() in self.whitelist

    def is_large_transfer(self, value_usd: float) -> bool:
        """Determines if transfer value breaches the large transfer threshold."""
        return value_usd >= self.config.large_transfer_threshold_usd

    def initiate_transfer_request(self, request: TransferRequest) -> VerificationResult:
        """
        Processes an incoming transfer request and evaluates whether a test transaction is mandatory.
        """
        audit = [f"Received transfer request {request.request_id} for {request.amount} {request.asset_symbol} (${request.value_usd:,.2f} USD)"]

        # 1. Whitelist validation
        if self.config.enforce_whitelisting and not self.is_whitelisted(request.recipient_address):
            audit.append(f"REJECTED: Recipient address {request.recipient_address} is not whitelisted.")
            logger.error(audit[-1])
            raise WhitelistError(f"Recipient address {request.recipient_address} is not whitelisted.")

        # 2. Asset config validation
        if request.asset_symbol not in self.asset_configs:
            raise VerificationError(f"Asset symbol '{request.asset_symbol}' is not registered.")

        asset_cfg = self.asset_configs[request.asset_symbol]

        # 3. Destination Tag check
        if asset_cfg.requires_destination_tag and not request.destination_tag:
            raise VerificationError(f"Asset {request.asset_symbol} requires a destination memo/tag.")

        self.pending_requests[request.request_id] = request

        # 4. Check if Large Transfer Threshold is breached
        if not self.is_large_transfer(request.value_usd):
            audit.append("Transfer below threshold. No test transaction required.")
            return VerificationResult(
                request_id=request.request_id,
                is_approved=True,
                status=VerificationStatus.NOT_REQUIRED,
                risk_level=RiskLevel.LOW,
                message="Transfer below large value threshold. Direct execution authorized.",
                audit_trail=audit,
            )

        # 5. Check if whitelisted bypass is allowed
        if self.config.allow_bypass_for_whitelisted and self.is_whitelisted(request.recipient_address):
            audit.append("Recipient whitelisted and bypass enabled. Direct execution authorized.")
            return VerificationResult(
                request_id=request.request_id,
                is_approved=True,
                status=VerificationStatus.APPROVED,
                risk_level=RiskLevel.MEDIUM,
                message="Whitelisted recipient bypass granted.",
                audit_trail=audit,
            )

        # Mandatory Test Transaction required
        audit.append(
            f"Mandatory dust test transaction required: Send {asset_cfg.test_amount} {request.asset_symbol} "
            f"to {request.recipient_address}"
        )
        logger.info(audit[-1])
        return VerificationResult(
            request_id=request.request_id,
            is_approved=False,
            status=VerificationStatus.TEST_PENDING,
            risk_level=RiskLevel.HIGH,
            message=f"Mandatory test transaction required ({asset_cfg.test_amount} {request.asset_symbol}).",
            audit_trail=audit,
        )

    def record_test_transaction(self, request_id: str, tx_hash: str) -> TestTransaction:
        """Records on-chain broadcast of a dust test transaction."""
        if request_id not in self.pending_requests:
            raise VerificationError(f"Request ID {request_id} not found in pending requests.")

        req = self.pending_requests[request_id]
        asset_cfg = self.asset_configs[req.asset_symbol]

        test_tx = TestTransaction(
            request_id=request_id,
            tx_hash=tx_hash,
            amount_sent=asset_cfg.test_amount,
            status=VerificationStatus.TEST_PENDING,
        )
        self.test_transactions[request_id] = test_tx
        logger.info(f"Recorded test transaction {tx_hash} for request {request_id}")
        return test_tx

    def update_test_confirmations(self, request_id: str, confirmations: int) -> TestTransaction:
        """
        Updates on-chain block confirmation depth for a pending test transaction.
        """
        if request_id not in self.test_transactions:
            raise VerificationError(f"No test transaction recorded for request ID {request_id}")

        req = self.pending_requests[request_id]
        asset_cfg = self.asset_configs[req.asset_symbol]
        test_tx = self.test_transactions[request_id]

        test_tx.confirmations = confirmations
        if confirmations >= asset_cfg.min_confirmations:
            test_tx.status = VerificationStatus.TEST_CONFIRMED
            test_tx.confirmed_at = datetime.datetime.now(datetime.timezone.utc)
            logger.info(
                f"Test transaction {test_tx.tx_hash} CONFIRMED with {confirmations}/{asset_cfg.min_confirmations} blocks."
            )
        return test_tx

    def verify_and_authorize_large_transfer(self, request_id: str) -> VerificationResult:
        """
        Final authorization gate: Validates that test transaction was confirmed within the allowed time window.
        """
        audit = []
        if request_id not in self.pending_requests:
            raise VerificationError(f"Request ID {request_id} not found.")

        req = self.pending_requests[request_id]
        audit.append(f"Evaluating final authorization for request {request_id}")

        if request_id not in self.test_transactions:
            raise TestTransactionPendingError("Test transaction has not been initiated.")

        test_tx = self.test_transactions[request_id]
        asset_cfg = self.asset_configs[req.asset_symbol]

        # 1. Check if test transaction is confirmed
        if test_tx.status != VerificationStatus.TEST_CONFIRMED or test_tx.confirmations < asset_cfg.min_confirmations:
            msg = f"Test transaction {test_tx.tx_hash} pending confirmation ({test_tx.confirmations}/{asset_cfg.min_confirmations} blocks)."
            audit.append(msg)
            raise TestTransactionPendingError(msg)

        # 2. Check time decay window (expiry)
        now = datetime.datetime.now(datetime.timezone.utc)
        elapsed = (now - test_tx.confirmed_at).total_seconds() / 60.0
        if elapsed > self.config.test_expiry_window_minutes:
            test_tx.status = VerificationStatus.EXPIRED
            msg = (
                f"Test transaction confirmation expired ({elapsed:.1f} mins ago, "
                f"max allowed: {self.config.test_expiry_window_minutes} mins). New test transaction required."
            )
            audit.append(msg)
            logger.error(msg)
            raise TestTransactionExpiredError(msg)

        audit.append(
            f"Test transaction {test_tx.tx_hash} verified ({test_tx.confirmations} confirmations, "
            f"confirmed {elapsed:.1f} mins ago)."
        )
        audit.append(f"AUTHORIZATION GRANTED: Execute primary transfer of {req.amount} {req.asset_symbol} (${req.value_usd:,.2f} USD)")

        logger.info(audit[-1])
        return VerificationResult(
            request_id=request_id,
            is_approved=True,
            status=VerificationStatus.APPROVED,
            risk_level=RiskLevel.LOW,
            message="Primary transfer authorized following successful test transaction verification.",
            test_tx_hash=test_tx.tx_hash,
            audit_trail=audit,
        )

