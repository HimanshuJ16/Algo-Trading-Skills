"""
headless-broker-auth-patterns: headless authentication helpers -- TOTP window safety
generator, SHA-256 checksum calculator, documented refresh-token exchange, browser
context manager for zombie cleanup, redirect-parameter session extraction, and
session-date-keyed token caching.

COMPLIANCE BOUNDARY (read before using the browser/TOTP paths against an Indian broker):
NSE circular NSE/INVG/67858 (05-May-2025), implementing SEBI circular
SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013 (04-Feb-2025) and fully applicable to all
stock brokers from 01-Apr-2026 (SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/132, 30-Sep-2025),
requires brokers to use "OAuth (Open Authentication) based authentication only" (Annexure
para I.c), to authenticate client API access "through two factor authentication" (I.d),
and mandates that "All API sessions shall be compulsorily logged out every day before the
start of the next trading day" (A.8) plus static-IP whitelisting per API key (A.1, I.e).
Zerodha states directly that automating the Kite Connect login "was never allowed to begin
with... you were in violation of the terms of use of the APIs."

Consequence for this module: `browser_login` and TOTP-driven credential posting are
provided for brokers/jurisdictions where the operator has confirmed they are permitted.
For Indian brokers the supported headless path is a broker-issued refresh token
(`fyers_refresh_token_login`) seeded by one interactive OAuth login -- not scripted
credential entry. See SKILL.md "When NOT to Use".

Checksum formulas below were verified against each broker's actual documented behavior
(Fyers appIdHash = sha256("appId:secret"); Zerodha checksum = sha256(api_key +
request_token + api_secret), per Kite Connect v3 docs) -- broker APIs change without
notice, so re-verify against current docs before relying on this in production.
"""
import datetime
from enum import Enum
import hashlib
import json
import logging
import os
import time
import base64
import hmac
import struct
import urllib.parse
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

try:
    import pyotp
except ImportError:
    pyotp = None

try:
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.common.by import By
except ImportError:  # pragma: no cover - selenium is an Archetype-B-only dependency
    WebDriverWait = None
    EC = None
    By = None

logger = logging.getLogger(__name__)


def generate_pure_python_totp(secret_b32: str) -> str:
    secret_clean = secret_b32.upper().replace(" ", "")
    padding = '=' * (-len(secret_clean) % 8)
    key = base64.b32decode(secret_clean + padding)
    intervals_no = int(time.time()) // 30
    msg = struct.pack(">Q", intervals_no)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    o = h[19] & 15
    h_int = (struct.unpack(">I", h[o:o+4])[0] & 0x7fffffff) % 1000000
    return f"{h_int:06d}"


class BrokerAuthError(RuntimeError):
    """Raised when a broker rejects an auth step or returns an unusable response.

    Brokers in this space routinely return HTTP 200 with an error *body* (Fyers returns
    ``{"s": "error", "code": -371, "message": "..."}``), so ``raise_for_status()`` alone
    does not catch a failed login. Surfacing the broker's own code/message here is what
    turns "KeyError: 'access_token'" into an actionable diagnosis.
    """


class AuthArchetype(str, Enum):
    """How a broker actually lets an unattended process obtain a working session.

    The archetype determines what "headless" can even mean for that broker; picking the
    wrong one is why integrations get built against endpoints that do not exist.
    """

    #: Scripted credential/TOTP post to a login endpoint that returns an auth code.
    #: Rarely an officially documented mechanism -- see the module compliance note.
    ARCHETYPE_A_REST = "ARCHETYPE_A_REST"
    #: Browser automation driving the broker's human login page (e.g. ICICI Breeze,
    #: which publishes no session-creation API).
    ARCHETYPE_B_BROWSER = "ARCHETYPE_B_BROWSER"
    #: One interactive OAuth login seeds a longer-lived refresh token that an unattended
    #: process exchanges for daily access tokens (Fyers: refresh token valid 15 days).
    ARCHETYPE_C_REFRESH_TOKEN = "ARCHETYPE_C_REFRESH_TOKEN"
    #: No login flow at all -- static key/secret sent as headers on every request
    #: (Alpaca). Nothing to cache; the whole problem is key custody.
    ARCHETYPE_D_STATIC_CREDENTIAL = "ARCHETYPE_D_STATIC_CREDENTIAL"
    #: A supervised long-running local gateway process holds the session; 2FA is
    #: approved out-of-band (IBKR TWS/Gateway via IBC, IBKR Mobile push).
    ARCHETYPE_E_SUPERVISED_GATEWAY = "ARCHETYPE_E_SUPERVISED_GATEWAY"


class TOTPHelper:
    """
    Generates TOTP codes with safety window checks to prevent TOTP expiration during HTTP transit.
    """

    #: RFC 6238 default time step. Both pyotp's default and generate_pure_python_totp
    #: use this; the safety-window arithmetic below assumes it.
    PERIOD_SEC = 30.0

    @staticmethod
    def get_totp_safe(totp_secret: str, min_remaining_sec: float = 5.0) -> str:
        """Return a 6-digit TOTP code with at least ``min_remaining_sec`` of its window left.

        Blocks (up to one time step) rather than returning a code that will expire in
        transit. ``min_remaining_sec`` must be inside the time step -- a value >= the
        period could never be satisfied and would silently degrade to "sleep, then return
        a code that still fails the caller's own requirement".
        """
        if not totp_secret or not totp_secret.strip():
            raise ValueError("totp_secret must be a non-empty base32 string")
        if not 0.0 <= min_remaining_sec < TOTPHelper.PERIOD_SEC:
            raise ValueError(
                f"min_remaining_sec must be in [0, {TOTPHelper.PERIOD_SEC}); "
                f"got {min_remaining_sec}"
            )

        now = time.time()
        time_remaining = TOTPHelper.PERIOD_SEC - (now % TOTPHelper.PERIOD_SEC)

        if time_remaining < min_remaining_sec:
            logger.info(f"TOTP near window expiry ({time_remaining:.1f}s remaining). Waiting for fresh window...")
            time.sleep(time_remaining + 0.5)

        if pyotp is not None:
            return pyotp.TOTP(totp_secret).now()
        return generate_pure_python_totp(totp_secret)



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
    """Caches access tokens keyed by *session* date, not by naive local calendar date.

    Why the distinction matters: a broker session does not roll over at the host's local
    midnight. Zerodha's Kite Connect access token "will expire at 6 AM on the next day
    (regulatory requirement)" (Kite Connect v3 docs), and NSE/INVG/67858 Annexure A.8
    requires all API sessions to be logged out before the start of the next trading day.
    Keying on ``datetime.now()`` in the host's local zone rolls the key at the wrong
    instant in both directions:

    * A UTC-hosted bot keys "today" for hours after the broker's IST session has already
      rolled, so it reuses a token the broker has flushed.
    * Between IST midnight and 06:00 the naive key advances while the token is still
      live, forcing a needless re-login -- which for an interactive-login broker means
      waking a human, and which can trip login rate limits.

    Pass ``session_tz`` (any ``tzinfo``; e.g. ``ZoneInfo("Asia/Kolkata")`` or a fixed
    ``timezone(timedelta(hours=5, minutes=30))``) and ``rollover_hour`` (6 for Kite) to
    key on the broker's real session boundary. The defaults preserve the previous
    naive-local behaviour, so existing callers are unaffected.
    """

    def __init__(
        self,
        cache_dir: str = ".auth_cache",
        session_tz: Optional[datetime.tzinfo] = None,
        rollover_hour: int = 0,
    ):
        if not 0 <= rollover_hour < 24:
            raise ValueError(f"rollover_hour must be in [0, 24); got {rollover_hour}")
        self.cache_dir = cache_dir
        self.session_tz = session_tz
        self.rollover_hour = rollover_hour
        # 0700: the files inside are 0600, but a world-traversable cache directory still
        # leaks which brokers this host authenticates to and on which days.
        os.makedirs(cache_dir, mode=0o700, exist_ok=True)
        try:
            os.chmod(cache_dir, 0o700)  # tighten a pre-existing loose directory
        except OSError as e:  # pragma: no cover - not meaningful on all filesystems
            logger.debug(f"Could not chmod cache dir {cache_dir}: {e}")

    def session_date(self, now: Optional[datetime.datetime] = None) -> datetime.date:
        """The broker session date that ``now`` belongs to.

        An instant before ``rollover_hour`` still belongs to the previous session date.
        ``now`` is injectable so this is testable without patching the clock.
        """
        if now is None:
            now = datetime.datetime.now(self.session_tz)
        elif self.session_tz is not None:
            now = now.astimezone(self.session_tz)
        if now.hour < self.rollover_hour:
            return (now - datetime.timedelta(days=1)).date()
        return now.date()

    def _get_cache_path(self, broker_name: str) -> str:
        return os.path.join(
            self.cache_dir, f"{broker_name}_{self.session_date().isoformat()}.json"
        )

    def get_cached_token(self, broker_name: str) -> Optional[str]:
        path = self._get_cache_path(broker_name)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("access_token")
            except (OSError, ValueError) as e:
                # ValueError covers json.JSONDecodeError (truncated/corrupt cache file).
                logger.warning(f"Failed to read auth cache {path}: {e}")
        return None

    def save_token(
        self,
        broker_name: str,
        access_token: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not access_token:
            raise ValueError("refusing to cache an empty access_token")
        path = self._get_cache_path(broker_name)
        payload = {
            "broker_name": broker_name,
            "access_token": access_token,
            "cached_at": datetime.datetime.now(self.session_tz).isoformat(),
            "session_date": self.session_date().isoformat(),
            "metadata": metadata or {},
        }
        # Write with restrictive permissions from the start rather than chmod after --
        # chmod-after leaves a brief window where the plaintext token is world-readable.
        # Write-then-rename so the swap is atomic: a multi-account fan-out or a systemd
        # restart racing another instance must never read a half-written cache file and
        # conclude the token is corrupt (which would trigger a needless re-login).
        tmp_path = f"{path}.tmp"
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.remove(tmp_path)
            except OSError:  # pragma: no cover - nothing to clean up
                pass
            raise
        self.purge_stale()

    def purge_stale(self, keep_sessions: int = 1) -> int:
        """Delete cache files older than the most recent ``keep_sessions`` session dates.

        Yesterday's file holds a plaintext bearer token the broker has already
        invalidated (NSE/INVG/67858 A.8 forces a daily logout), so retaining it is
        credential-leak surface with no operational benefit. Returns the number removed.
        """
        cutoff = self.session_date() - datetime.timedelta(days=max(keep_sessions - 1, 0))
        removed = 0
        try:
            entries = os.listdir(self.cache_dir)
        except OSError as e:  # pragma: no cover - cache dir vanished underneath us
            logger.warning(f"Could not list auth cache dir {self.cache_dir}: {e}")
            return 0
        for entry in entries:
            if not entry.endswith(".json") or "_" not in entry:
                continue
            date_part = entry[: -len(".json")].rsplit("_", 1)[-1]
            try:
                file_date = datetime.date.fromisoformat(date_part)
            except ValueError:
                continue  # not one of ours; leave it alone
            if file_date < cutoff:
                try:
                    os.remove(os.path.join(self.cache_dir, entry))
                    removed += 1
                except OSError as e:  # pragma: no cover
                    logger.warning(f"Could not remove stale auth cache {entry}: {e}")
        if removed:
            logger.info(f"Purged {removed} stale auth cache file(s) from {self.cache_dir}.")
        return removed


#: Query-parameter names brokers use to hand back a session/auth token on the redirect.
#: ICICI Breeze uses ``API_Session`` (exact casing per ICICI Direct's own documentation);
#: matching is case-insensitive here precisely because that casing is easy to get wrong.
DEFAULT_REDIRECT_TOKEN_PARAMS: Tuple[str, ...] = ("API_Session", "session_token", "apisession")


def extract_session_token(
    redirect_url: str,
    param_names: Iterable[str] = DEFAULT_REDIRECT_TOKEN_PARAMS,
) -> str:
    """Extract the session/auth token from a post-login redirect URL.

    Parses the query string properly (``urllib.parse``) instead of slicing on a literal
    ``"session_token="``. String-slicing is not merely inelegant here, it is silently
    wrong: on a Breeze redirect such as
    ``https://host/cb?API_Session=abc123``, ``url.split("session_token=")[-1].split("&")[0]``
    finds no separator, returns the *entire URL prefix*, and hands that back as though it
    were a token. The failure then surfaces much later as an opaque broker rejection.

    Matching is case-insensitive (``API_Session`` vs ``api_session``) and the value is
    URL-decoded. Raises BrokerAuthError if no candidate parameter carries a non-empty
    value, so a changed login page fails loudly at the point of breakage.
    """
    if not redirect_url:
        raise BrokerAuthError("No redirect URL captured after login; nothing to extract.")
    wanted = {n.lower() for n in param_names}
    query = urllib.parse.urlparse(redirect_url).query
    # keep_blank_values so an explicitly empty param is reported as empty, not "missing".
    params = urllib.parse.parse_qs(query, keep_blank_values=True)
    for key, values in params.items():
        if key.lower() in wanted:
            for value in values:
                if value:
                    return value
    raise BrokerAuthError(
        f"Redirect URL carried none of {sorted(wanted)} with a non-empty value. "
        f"Present parameters: {sorted(params)}. The broker's login page or redirect "
        f"contract has probably changed -- re-check it rather than retrying."
    )


def _require_json_field(resp: Any, field: str, context: str) -> Any:
    """Pull ``field`` out of a broker JSON response, or raise a diagnosable error.

    ``raise_for_status()`` is not sufficient for these APIs: Fyers returns HTTP 200 with
    ``{"s": "error", "code": -371, "message": "Please provide sha256 hash of appId and
    app secret"}``. Without this, the caller sees ``KeyError: 'access_token'`` and has no
    pointer back to the checksum -- exactly the misdiagnosis referenced in
    references/workflows.md.
    """
    try:
        data = resp.json()
    except ValueError as exc:
        raise BrokerAuthError(f"{context}: broker returned a non-JSON body.") from exc
    if not isinstance(data, dict):
        raise BrokerAuthError(f"{context}: expected a JSON object, got {type(data).__name__}.")
    value = data.get(field)
    if not value:
        detail = ", ".join(
            f"{k}={data[k]!r}" for k in ("s", "status", "code", "message", "error", "error_description")
            if k in data
        )
        # Truncated: the fallback echoes the whole body, which may carry other
        # credential-shaped fields we should not splash into logs wholesale.
        fallback = repr(data)
        if len(fallback) > 200:
            fallback = fallback[:200] + "... (truncated)"
        raise BrokerAuthError(
            f"{context}: response contained no usable {field!r}. "
            f"Broker said: {detail or fallback}"
        )
    return value


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
    auth_code = _require_json_field(resp, "auth_code", "Archetype A step 1 (TOTP login)")

    # appIdHash is computed from client_id + secret only -- auth_code is sent separately
    # as `code`, never hashed. See ChecksumHelper.fyers_checksum docstring.
    checksum = ChecksumHelper.fyers_checksum(client_id, secret)
    resp = session.post(
        f"{base_url}/login/token",
        json={"client_id": client_id, "code": auth_code, "checksum": checksum},
        timeout=10,
    )
    resp.raise_for_status()
    return _require_json_field(resp, "access_token", "Archetype A step 2 (token exchange)")


def fyers_refresh_token_login(
    refresh_token: str,
    app_id: str,
    secret_key: str,
    pin: str,
    session: Any,
    base_url: str = "https://api-t1.fyers.in/api/v3",
) -> str:
    """Archetype C: exchange a Fyers refresh token for a fresh access token.

    This is the *documented*, broker-supported way to run unattended against Fyers, and
    the one an integration should reach for first. One interactive OAuth login seeds a
    refresh token that "remains valid for 15 days" (Fyers support KB, "What is the
    function of the refresh token in FYERS?"); the access token it mints is a one-day
    token, so this runs once per session date. No browser, no stored password, no
    scripted credential entry -- and therefore none of the terms-of-use exposure that the
    TOTP/browser paths carry.

    Its limit is equally important: after 15 days a human must complete the OAuth login
    again. Anything claiming indefinite unattended Fyers auth is doing something the
    broker has not sanctioned. Plan the interactive re-seed rather than being surprised
    by it mid-week.

    ``pin`` is the account's trading PIN, required by the endpoint alongside the
    appIdHash. Treat it as a secret of the same grade as the API secret.
    """
    if not refresh_token:
        raise ValueError("refresh_token must be non-empty")
    if not pin:
        raise ValueError("pin is required by the Fyers validate-refresh-token endpoint")

    payload = {
        "grant_type": "refresh_token",
        "appIdHash": ChecksumHelper.fyers_checksum(app_id, secret_key),
        "refresh_token": refresh_token,
        "pin": pin,
    }
    resp = session.post(f"{base_url}/validate-refresh-token", json=payload, timeout=10)
    resp.raise_for_status()
    return _require_json_field(resp, "access_token", "Fyers refresh-token exchange")


def browser_login(
    login_url: str,
    username: str,
    password: str,
    totp_secret: Optional[str],
    headless_driver_factory: Callable[[], Any],
    element_timeout_sec: float = 15.0,
    redirect_param_names: Iterable[str] = DEFAULT_REDIRECT_TOKEN_PARAMS,
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

        # Wait for the redirect itself rather than a fixed sleep: poll until the token
        # parameter is actually extractable, bounded by element_timeout_sec via
        # WebDriverWait's own polling loop. Polling on "can I extract it?" rather than on
        # a substring means the wait and the extraction cannot disagree about what counts
        # as a successful redirect.
        def _redirect_carries_token(d: Any) -> bool:
            try:
                extract_session_token(d.current_url, redirect_param_names)
            except BrokerAuthError:
                return False
            return True

        wait.until(_redirect_carries_token)
        return extract_session_token(driver.current_url, redirect_param_names)


def get_valid_session(
    broker_name: str,
    archetype: AuthArchetype,
    login_fn: Callable[[], str],
    probe_fn: Optional[Callable[[str], bool]] = None,
    cache_manager: Optional[TokenCacheManager] = None,
) -> str:
    """Orchestrates the full documented sequence: check cache -> live-probe the cached
    token -> only call login_fn (fyers_refresh_token_login / rest_login / browser_login,
    pre-bound via a lambda/partial by the caller) if no cached token exists or the probe
    says it's invalid.

    Pass a cache_manager built with the broker's session timezone and rollover hour
    (see TokenCacheManager) -- the default naive-local key rolls at the wrong instant for
    any broker whose session boundary is not the host's midnight.

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
