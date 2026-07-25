"""
headless-broker-auth-patterns: Production-grade dual-archetype headless authentication dispatcher,
TOTP window safety generator, SHA-256 checksum calculator, browser context manager for zombie cleanup,
and daily date-keyed token caching.

Checksum formulas below were verified against each broker's actual documented/observed
behavior (Fyers' community-confirmed -371 error resolution, Zerodha's official pykiteconnect
source) rather than assumed from general pattern knowledge -- broker APIs change without
notice, so re-verify against current docs before relying on this in production.
"""
import datetime
from enum import Enum
import hashlib
import json
import logging
import os
import time
from typing import Any, Callable, Dict, Optional
import pyotp

try:
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.common.by import By
except ImportError:  # pragma: no cover - selenium is an Archetype-B-only dependency
    WebDriverWait = None
    EC = None
    By = None

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
    """Generates SHA-256 checksum signatures required by REST brokers (Fyers/Zerodha).

    IMPORTANT: Fyers' appIdHash is computed from app_id + secret_key ONLY. auth_code is
    NOT part of the hash -- it is sent as a separate `code` field in the token-exchange
    payload. Including auth_code in the hash (an earlier version of this file did) produces
    a checksum Fyers rejects with error -371 ("Please provide sha256 hash of appId and app
    secret"), since the hash Fyers computes server-side to compare against never includes it.
    """

    @staticmethod
    def fyers_checksum(app_id: str, secret_key: str) -> str:
        raw = f"{app_id}:{secret_key}"
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
        # Write with restrictive permissions from the start rather than chmod after --
        # chmod-after leaves a brief window where the plaintext token is world-readable.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)


# Backward compatibility functions
def rest_login(base_url: str, client_id: str, secret: str, totp_secret: str, session: Any) -> str:
    """Archetype A: REST-based headless auth (e.g. Fyers-style).

    NOTE: this is an illustrative two-step template, not a literal drop-in Fyers client --
    Fyers' real flow involves additional steps (verify_client_id, TOTP verification, PIN
    verification via their vagator endpoints) beyond this simplified step1/token shape.
    The ChecksumHelper.fyers_checksum formula itself IS the verified, correct part; adapt
    the request shape to the specific broker's actual documented endpoints.
    """
    code = TOTPHelper.get_totp_safe(totp_secret)
    resp = session.post(f"{base_url}/login/step1", json={"client_id": client_id, "totp": code}, timeout=10)
    resp.raise_for_status()
    auth_code = resp.json()["auth_code"]

    # appIdHash is computed from client_id + secret only -- auth_code is sent separately
    # as `code`, never hashed. See ChecksumHelper.fyers_checksum docstring.
    checksum = ChecksumHelper.fyers_checksum(client_id, secret)
    resp = session.post(
        f"{base_url}/login/token",
        json={"client_id": client_id, "code": auth_code, "checksum": checksum},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def browser_login(
    login_url: str,
    username: str,
    password: str,
    totp_secret: Optional[str],
    headless_driver_factory: Callable[[], Any],
    element_timeout_sec: float = 15.0,
) -> str:
    """Archetype B: browser-automation login for brokers with no REST login API.

    Uses WebDriverWait for every element interaction (never a fixed time.sleep) and a
    bounded per-element timeout, so a slow page load or a login-page redesign that
    removes an expected element fails fast with a clear TimeoutException rather than
    hanging the entire auth pipeline indefinitely -- see SKILL.md Common Pitfalls.
    """
    if WebDriverWait is None:
        raise ImportError(
            "selenium is required for browser_login (Archetype B). Install with: pip install selenium"
        )

    with HeadlessBrowserContext(headless_driver_factory) as driver:
        driver.set_page_load_timeout(element_timeout_sec)
        wait = WebDriverWait(driver, element_timeout_sec)

        driver.get(login_url)
        wait.until(EC.visibility_of_element_located((By.ID, "username"))).send_keys(username)
        wait.until(EC.visibility_of_element_located((By.ID, "password"))).send_keys(password)
        wait.until(EC.element_to_be_clickable((By.ID, "submit"))).click()

        if totp_secret:
            code = TOTPHelper.get_totp_safe(totp_secret)
            wait.until(EC.visibility_of_element_located((By.ID, "totp"))).send_keys(code)
            wait.until(EC.element_to_be_clickable((By.ID, "totp-submit"))).click()

        # Wait for the redirect itself rather than a fixed sleep: poll until the URL
        # contains the token param, bounded by element_timeout_sec via WebDriverWait's
        # own polling loop.
        wait.until(lambda d: "session_token=" in d.current_url or "api_session=" in d.current_url)
        redirect_url = driver.current_url
        session_token = redirect_url.split("session_token=")[-1].split("&")[0]
        return session_token


def get_valid_session(
    broker_name: str,
    archetype: AuthArchetype,
    login_fn: Callable[[], str],
    probe_fn: Optional[Callable[[str], bool]] = None,
    cache_manager: Optional[TokenCacheManager] = None,
) -> str:
    """Orchestrates the full documented sequence: check cache -> live-probe the cached
    token -> only call login_fn (rest_login/browser_login, pre-bound via a lambda/partial
    by the caller) if no cached token exists or the probe says it's invalid.

    This closes the gap between what SKILL.md/checklist.md document ("attempt to reuse
    the cached token via a live probe... before triggering a fresh login") and what the
    module previously provided no example of composing.

    probe_fn(token) -> bool should perform a cheap, read-only, side-effect-free API call
    (see the token-lifecycle-live-probing skill) and return True only on a confirmed-valid
    response. If probe_fn is None, the cached token is used without validation -- callers
    should treat this as an explicit opt-out of the live-probe discipline, not a default.
    """
    cache_manager = cache_manager or TokenCacheManager()
    cached_token = cache_manager.get_cached_token(broker_name)

    if cached_token is not None:
        if probe_fn is None:
            logger.warning(
                f"[{broker_name}] Reusing cached token with no probe_fn supplied -- "
                f"validity is unverified. See token-lifecycle-live-probing skill."
            )
            return cached_token
        if probe_fn(cached_token):
            logger.info(f"[{broker_name}] Cached token passed live probe, reusing.")
            return cached_token
        logger.info(f"[{broker_name}] Cached token failed live probe, re-authenticating.")
    else:
        logger.info(f"[{broker_name}] No cached token found, authenticating.")

    new_token = login_fn()
    cache_manager.save_token(broker_name, new_token, metadata={"archetype": archetype.value})
    return new_token
