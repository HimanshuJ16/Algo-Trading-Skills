"""
headless-broker-auth-patterns: Production-grade dual-archetype headless authentication dispatcher,
TOTP window safety generator, SHA-256 checksum calculator, browser context manager for zombie cleanup,
and daily date-keyed token caching.
"""
from dataclasses import dataclass
import datetime
from enum import Enum
import hashlib
import json
import logging
import os
import time
from typing import Any, Callable, Dict, Optional, Tuple
import pyotp

logger = logging.getLogger(__name__)


class AuthArchetype(str, Enum):
    ARCHETYPE_A_REST = "ARCHETYPE_A_REST"
    ARCHETYPE_B_BROWSER = "ARCHETYPE_B_BROWSER"


class TOTPHelper:
    """
    Generates TOTP codes with safety window checks to prevent TOTP expiration during HTTP transit.
    """

    @staticmethod
    def get_totp_safe(totp_secret: str, min_remaining_sec: float = 5.0) -> str:
        totp = pyotp.TOTP(totp_secret)
        now = time.time()
        time_remaining = 30.0 - (now % 30.0)

        if time_remaining < min_remaining_sec:
            logger.info(f"TOTP near window expiry ({time_remaining:.1f}s remaining). Waiting for fresh window...")
            time.sleep(time_remaining + 0.5)

        return pyotp.TOTP(totp_secret).now()


class ChecksumHelper:
    """Generates SHA-256 checksum signatures required by REST brokers (Fyers/Zerodha)."""

    @staticmethod
    def fyers_checksum(app_id: str, auth_code: str, secret_key: str) -> str:
        raw = f"{app_id}:{auth_code}:{secret_key}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def zerodha_checksum(api_key: str, request_token: str, api_secret: str) -> str:
        raw = f"{api_key}{request_token}{api_secret}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class HeadlessBrowserContext:
    """
    Context manager for browser automation drivers ensuring driver.quit() is strictly called,
    preventing orphaned zombie Chrome processes under systemd/cron restarts.
    """

    def __init__(self, driver_factory: Callable[[], Any]):
        self.driver_factory = driver_factory
        self.driver = None

    def __enter__(self):
        self.driver = self.driver_factory()
        return self.driver

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.driver:
            try:
                self.driver.quit()
                logger.info("Headless browser driver closed cleanly.")
            except Exception as e:
                logger.warning(f"Error quitting browser driver: {e}")


class TokenCacheManager:
    """Caches access tokens keyed by trading date (YYYY-MM-DD)."""

    def __init__(self, cache_dir: str = ".auth_cache"):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def _get_cache_path(self, broker_name: str) -> str:
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.cache_dir, f"{broker_name}_{today}.json")

    def get_cached_token(self, broker_name: str) -> Optional[str]:
        path = self._get_cache_path(broker_name)
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                    return data.get("access_token")
            except Exception as e:
                logger.warning(f"Failed to read auth cache {path}: {e}")
        return None

    def save_token(self, broker_name: str, access_token: str, metadata: Optional[Dict[str, Any]] = None):
        path = self._get_cache_path(broker_name)
        payload = {
            "broker_name": broker_name,
            "access_token": access_token,
            "cached_at": datetime.datetime.now().isoformat(),
            "metadata": metadata or {},
        }
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)


# Backward compatibility functions
def rest_login(base_url, client_id, secret, totp_secret, session):
    """Archetype A: REST-based headless auth (e.g. Fyers-style)."""
    code = TOTPHelper.get_totp_safe(totp_secret)
    resp = session.post(f"{base_url}/login/step1", json={"client_id": client_id, "totp": code}, timeout=10)
    resp.raise_for_status()
    auth_code = resp.json()["auth_code"]

    checksum = ChecksumHelper.fyers_checksum(client_id, auth_code, secret)
    resp = session.post(
        f"{base_url}/login/token",
        json={"client_id": client_id, "secret": secret, "auth_code": auth_code, "checksum": checksum},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def browser_login(login_url, username, password, totp_secret, headless_driver_factory):
    """Archetype B: browser-automation login for brokers with no REST login API."""
    with HeadlessBrowserContext(headless_driver_factory) as driver:
        driver.get(login_url)
        driver.find_element("id", "username").send_keys(username)
        driver.find_element("id", "password").send_keys(password)
        driver.find_element("id", "submit").click()

        if totp_secret:
            code = TOTPHelper.get_totp_safe(totp_secret)
            time.sleep(0.5)
            driver.find_element("id", "totp").send_keys(code)
            driver.find_element("id", "totp-submit").click()

        time.sleep(1.0)
        redirect_url = driver.current_url
        session_token = redirect_url.split("session_token=")[-1].split("&")[0]
        return session_token
