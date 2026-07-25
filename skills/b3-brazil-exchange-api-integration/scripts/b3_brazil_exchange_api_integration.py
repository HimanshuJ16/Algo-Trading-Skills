"""
b3-brazil-exchange-api-integration:
Connectivity manager for the B3 PUMA Trading System, enforcing
configuration rules for Legacy (FIX/FAST) and Modern (SBE) protocols.
"""
import dataclasses
import logging
from enum import Enum

logger = logging.getLogger(__name__)


class B3ProtocolSuite(Enum):
    LEGACY_FIX_FAST = "LEGACY_FIX_FAST"    # FIX 4.4 Order Entry + UMDF FAST Market Data
    MODERN_BINARY_SBE = "MODERN_BINARY_SBE" # FIXP Binary Order Entry + UMDF SBE Market Data


@dataclasses.dataclass
class B3ConnectionConfig:
    comp_id: str
    password: str
    order_entry_ip: str
    market_data_multicast_ip: str
    protocol_suite: B3ProtocolSuite
    enable_application_gap_recovery: bool # Crucial for UDP SBE feeds


class B3ConnectionState(Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    FAILED = "FAILED"


class B3IntegrationEngine:
    """
    Manages the lifecycle and validates the topology of a direct connection to B3.
    """

    def __init__(self, config: B3ConnectionConfig):
        self.config = config
        self.state = B3ConnectionState.DISCONNECTED
        self._validate_b3_architecture()

    def _validate_b3_architecture(self):
        """Validates that the configuration matches B3 PUMA architectural constraints."""
        if self.config.protocol_suite == B3ProtocolSuite.MODERN_BINARY_SBE:
            # B3's Binary UMDF feed operates on UDP without a native TCP recovery channel.
            # If the application doesn't implement gap recovery, missing packets are fatal.
            if not self.config.enable_application_gap_recovery:
                logger.error("Invalid Configuration: B3 MODERN_BINARY_SBE requires application-level gap recovery.")
                raise ValueError("Binary SBE feeds lack TCP recovery; enable_application_gap_recovery must be True.")

    def connect(self) -> bool:
        if self.state == B3ConnectionState.CONNECTED:
            logger.info("B3 Session already connected.")
            return True

        self.state = B3ConnectionState.CONNECTING
        logger.info(f"Initiating B3 {self.config.protocol_suite.value} connection for {self.config.comp_id}...")
        
        # In a real implementation, this would trigger the OnixS/EPAM SDK 
        # or custom socket initialization for FIXP / FAST.
        
        self.state = B3ConnectionState.CONNECTED
        logger.info(f"B3 {self.config.protocol_suite.value} Session Connected successfully.")
        return True

    def disconnect(self) -> bool:
        if self.state == B3ConnectionState.DISCONNECTED:
            return True
            
        logger.info(f"Disconnecting B3 {self.config.protocol_suite.value} session...")
        self.state = B3ConnectionState.DISCONNECTED
        return True
