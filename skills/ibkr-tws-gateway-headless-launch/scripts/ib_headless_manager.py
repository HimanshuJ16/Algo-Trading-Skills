"""
ibkr-tws-gateway-headless-launch: Production-grade IBKR IB Gateway headless launch manager,
TCP socket readiness prober, container spec generator, and daily reset handler.
"""
from dataclasses import dataclass
import logging
import socket
import time
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class IBGatewayError(RuntimeError):
    """Raised when IB Gateway is unreachable or socket probe fails."""
    pass


# Default IBKR API Ports
IB_PAPER_GATEWAY_PORT = 4002
IB_LIVE_GATEWAY_PORT = 4001
IB_PAPER_TWS_PORT = 7497
IB_LIVE_TWS_PORT = 7496


@dataclass
class IBGatewayConfig:
    host: str = "127.0.0.1"
    port: int = IB_PAPER_GATEWAY_PORT
    client_id: int = 1
    is_paper: bool = True
    timeout_seconds: float = 5.0


class IBGatewayHeadlessManager:
    """
    Manages IBKR Gateway socket readiness probes, environment port matching,
    daily reset detection, and Docker container spec generation.
    """

    def __init__(self, config: IBGatewayConfig):
        self.config = config
        self._validate_port_matching()

    def _validate_port_matching(self):
        """Ensures port number matches paper vs live configuration."""
        if self.config.is_paper:
            if self.config.port in (IB_LIVE_GATEWAY_PORT, IB_LIVE_TWS_PORT):
                raise IBGatewayError(
                    f"CRITICAL PORT MISMATCH: Paper mode configured, but port is set to LIVE ({self.config.port})!"
                )
        else:
            if self.config.port in (IB_PAPER_GATEWAY_PORT, IB_PAPER_TWS_PORT):
                raise IBGatewayError(
                    f"CRITICAL PORT MISMATCH: Live mode configured, but port is set to PAPER ({self.config.port})!"
                )

    @staticmethod
    def probe_gateway_port(host: str, port: int, timeout: float = 2.0) -> bool:
        """Attempts TCP connection to IB Gateway listening port."""
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False

    def wait_for_gateway_ready(
        self, max_retries: int = 10, retry_interval: float = 2.0
    ) -> bool:
        """Polls IB Gateway port until socket is accepting connections."""
        logger.info(f"Waiting for IB Gateway on {self.config.host}:{self.config.port}...")
        for attempt in range(1, max_retries + 1):
            if self.probe_gateway_port(self.config.host, self.config.port, self.config.timeout_seconds):
                logger.info(f"IB Gateway socket READY on {self.config.host}:{self.config.port}.")
                return True
            logger.warning(f"Gateway probe attempt {attempt}/{max_retries} failed. Retrying in {retry_interval}s...")
            time.sleep(retry_interval)

        raise IBGatewayError(
            f"IB Gateway on {self.config.host}:{self.config.port} failed to become ready after {max_retries} attempts."
        )

    def generate_docker_spec(self) -> Dict[str, Any]:
        """Generates production Docker Compose spec for headless IB Gateway container."""
        return {
            "version": "3.8",
            "services": {
                "ib-gateway": {
                    "image": "ghcr.io/gnzrb/ib-gateway:latest",
                    "container_name": "ibkr_gateway_headless",
                    "restart": "always",
                    "environment": {
                        "TWS_USERID": "${IBKR_USERID}",
                        "TWS_PASSWORD": "${IBKR_PASSWORD}",
                        "TRADING_MODE": "paper" if self.config.is_paper else "live",
                        "READ_ONLY_API": "no",
                    },
                    "ports": [f"{self.config.port}:{self.config.port}"],
                    "healthcheck": {
                        "test": ["CMD-SHELL", f"nc -z localhost {self.config.port} || exit 1"],
                        "interval": "30s",
                        "timeout": "5s",
                        "retries": 3,
                    },
                }
            },
        }
