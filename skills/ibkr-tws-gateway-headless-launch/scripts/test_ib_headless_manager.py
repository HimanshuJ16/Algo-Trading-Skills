"""
Unit tests for ibkr-tws-gateway-headless-launch skill.

Tests:
1. Port validation matching paper vs live environment.
2. Rejection of paper mode with live API port.
3. TCP socket readiness probe logic.
4. Docker Compose spec generation.
"""
import socket
import threading
import time
import unittest
from ib_headless_manager import (
    IB_LIVE_GATEWAY_PORT,
    IB_PAPER_GATEWAY_PORT,
    IBGatewayConfig,
    IBGatewayError,
    IBGatewayHeadlessManager,
)


class TestIBKRGatewayHeadlessManager(unittest.TestCase):

    def test_valid_paper_config(self):
        cfg = IBGatewayConfig(is_paper=True, port=IB_PAPER_GATEWAY_PORT)
        mgr = IBGatewayHeadlessManager(cfg)
        self.assertEqual(mgr.config.port, 4002)

    def test_port_mismatch_rejection(self):
        # Paper mode with Live port -> error
        flawed = IBGatewayConfig(is_paper=True, port=IB_LIVE_GATEWAY_PORT)
        with self.assertRaises(IBGatewayError):
            IBGatewayHeadlessManager(flawed)

    def test_socket_probe_logic(self):
        # Test socket probe against closed port
        self.assertFalse(IBGatewayHeadlessManager.probe_gateway_port("127.0.0.1", 59999, timeout=0.1))

        # Spin up dummy TCP listener on local port
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        _, port = server.getsockname()
        server.listen(1)

        try:
            self.assertTrue(IBGatewayHeadlessManager.probe_gateway_port("127.0.0.1", port, timeout=0.5))
        finally:
            server.close()

    def test_docker_spec_generation(self):
        cfg = IBGatewayConfig(is_paper=True, port=IB_PAPER_GATEWAY_PORT)
        mgr = IBGatewayHeadlessManager(cfg)
        spec = mgr.generate_docker_spec()
        self.assertIn("ib-gateway", spec["services"])
        self.assertEqual(spec["services"]["ib-gateway"]["environment"]["TRADING_MODE"], "paper")


if __name__ == "__main__":
    unittest.main()
