"""
systemd-supervision-for-trading-bots: Production-grade systemd watchdog ping helper,
pre-market healthcheck validator, systemd unit file parser, and graceful shutdown signal handler.
"""
from dataclasses import dataclass
import datetime
import logging
import os
import socket
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class PreMarketHealthCheckError(RuntimeError):
    """Raised when pre-market healthcheck fails before starting trading bot."""
    pass


@dataclass
class HealthCheckResult:
    passed: bool
    checks: Dict[str, bool]
    details: List[str]


class SystemdSupervisionHelper:
    """
    Supervises live bot processes using systemd sd_notify protocols,
    watchdog keepalive pings, and pre-market health checks.
    """

    def __init__(self, notify_socket_path: Optional[str] = None):
        self.notify_socket_path = notify_socket_path or os.environ.get("NOTIFY_SOCKET")

    def sd_notify(self, state: str) -> bool:
        """
        Sends status notification string to systemd NOTIFY_SOCKET.
        """
        if not self.notify_socket_path:
            logger.debug(f"systemd notify disabled (no NOTIFY_SOCKET): {state}")
            return False

        try:
            addr = self.notify_socket_path
            if addr.startswith("@"):
                addr = "\0" + addr[1:]

            sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            try:
                sock.connect(addr)
                sock.sendall(state.encode("utf-8"))
                return True
            finally:
                sock.close()
        except Exception as e:
            logger.warning(f"Failed to send systemd notification '{state}': {e}")
            return False

    def notify_ready(self, status: str = "Trading bot initialized and ready."):
        self.sd_notify(f"READY=1\nSTATUS={status}")

    def notify_watchdog(self):
        self.sd_notify("WATCHDOG=1")

    def notify_stopping(self, status: str = "Trading bot shutting down cleanly."):
        self.sd_notify(f"STOPPING=1\nSTATUS={status}")

    @staticmethod
    def run_premarket_healthcheck(
        secrets_dict: Dict[str, str],
        broker_connectivity_fn: Callable[[], bool],
        is_holiday_fn: Optional[Callable[[datetime.date], bool]] = None,
        as_of_date: Optional[datetime.date] = None,
    ) -> HealthCheckResult:
        """
        Executes pre-market checks (ExecStartPre) before starting the main process.
        """
        today = as_of_date or datetime.date.today()
        checks = {}
        details = []

        # 1. Validate Secrets & API Keys
        required_keys = ["BROKER_API_KEY", "BROKER_SECRET"]
        missing_keys = [k for k in required_keys if not secrets_dict.get(k)]
        pass_secrets = len(missing_keys) == 0
        checks["secrets_present"] = pass_secrets
        if not pass_secrets:
            details.append(f"Missing required secrets: {missing_keys}")

        # 2. Market Holiday Check
        is_holiday = is_holiday_fn(today) if is_holiday_fn else False
        checks["not_market_holiday"] = not is_holiday
        if is_holiday:
            details.append(f"Market is closed today ({today}) for exchange holiday.")

        # 3. Broker Connectivity Check
        try:
            pass_conn = broker_connectivity_fn()
        except Exception as e:
            pass_conn = False
            details.append(f"Broker connectivity exception: {e}")
        checks["broker_connectivity"] = pass_conn

        all_passed = all(checks.values())
        if not all_passed:
            logger.error(f"Pre-market healthcheck FAILED: {details}")

        return HealthCheckResult(passed=all_passed, checks=checks, details=details)

    @staticmethod
    def validate_unit_file_content(unit_content: str) -> Tuple[bool, List[str]]:
        """Validates systemd unit file directives for trading bot requirements."""
        issues = []
        lines = [line.strip() for line in unit_content.splitlines() if line.strip() and not line.startswith("#")]

        required_directives = [
            "Restart=on-failure",
            "WatchdogSec=",
            "StartLimitBurst=",
            "MemoryMax=",
            "ExecStartPre=",
        ]

        for req in required_directives:
            if not any(req in line for line in lines):
                issues.append(f"Missing directive: '{req}'")

        if "Restart=always" in unit_content:
            issues.append("Avoid 'Restart=always'; use 'Restart=on-failure' with burst limits.")

        return len(issues) == 0, issues
