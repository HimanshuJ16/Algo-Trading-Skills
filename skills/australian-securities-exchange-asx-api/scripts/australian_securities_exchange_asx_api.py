"""
australian-securities-exchange-asx-api:
Integration adapter managing connectivity configurations and topology validation
for ASX FIX 5.0, OUCH, and ITCH protocols.
"""
import dataclasses
import logging
from enum import Enum

logger = logging.getLogger(__name__)


class AsxProtocol(Enum):
    FIX_5_0_SP2 = "FIX_5_0_SP2"  # Standard order entry and drop copy
    OUCH = "OUCH"                # Ultra-low latency binary order entry
    ITCH = "ITCH"                # Ultra-low latency multicast market data


@dataclasses.dataclass
class AsxConnectionConfig:
    host: str
    port: int
    comp_id: str
    protocol: AsxProtocol
    is_alc_colocated: bool       # True if servers are physically in the Australian Liquidity Centre
    is_cde_environment: bool     # True if connecting to Customer Development Environment


class AsxConnectionState(Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    FAILED = "FAILED"


class AsxIntegrationEngine:
    """
    Manages the lifecycle and validation of a direct connection to the ASX.
    """

    def __init__(self, config: AsxConnectionConfig):
        self.config = config
        self.state = AsxConnectionState.DISCONNECTED
        self._validate_topology()

    def _validate_topology(self):
        """Validates that the selected protocol matches the physical network topology."""
        if self.config.protocol in [AsxProtocol.OUCH, AsxProtocol.ITCH]:
            if not self.config.is_alc_colocated:
                logger.error(f"Invalid Topology: ASX {self.config.protocol.value} requires ALC Co-Location.")
                raise ValueError(f"Cannot route {self.config.protocol.value} outside the ALC.")
                
        if not self.config.is_cde_environment and self.config.host.startswith("test"):
            logger.warning("Host appears to be a test environment, but is_cde_environment is False.")

    def connect(self) -> bool:
        if self.state == AsxConnectionState.CONNECTED:
            logger.info("ASX Session already connected.")
            return True

        self.state = AsxConnectionState.CONNECTING
        logger.info(f"Initiating ASX {self.config.protocol.value} connection to {self.config.host}:{self.config.port}...")
        
        # In a real implementation, this would trigger the python-quickfix socket 
        # or the raw TCP socket for OUCH.
        
        self.state = AsxConnectionState.CONNECTED
        logger.info(f"ASX {self.config.protocol.value} Session Connected successfully for {self.config.comp_id}.")
        return True

    def disconnect(self) -> bool:
        if self.state == AsxConnectionState.DISCONNECTED:
            return True
            
        logger.info(f"Disconnecting ASX {self.config.protocol.value} session...")
        self.state = AsxConnectionState.DISCONNECTED
        return True
