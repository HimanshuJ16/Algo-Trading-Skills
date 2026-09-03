"""alpaca-paper-live-key-separation: Production-grade Alpaca API environment segregation guard,
credential prefix validator (PK... vs AK...), base URL endpoint matcher,
and live trading safety gate to prevent accidental live capital loss.

Authoritative control
---------------------
The *base URL* is the control that actually separates environments: Alpaca serves
paper accounts from ``https://paper-api.alpaca.markets`` and live accounts from
``https://api.alpaca.markets``. A live account is not reachable through the paper
host. The credential-prefix check and the account probe in this module are
defence-in-depth on top of that pin, not replacements for it.

Verified against the Alpaca Trading API v2 account schema (see
``references/standards.md``): GET /v2/account does **not** return an ``is_paper``
field. ``probe_account`` therefore resolves the environment from the signals the
API really exposes, and reports "undeterminable" rather than inventing a value.
"""
from dataclasses import dataclass
from enum import Enum
import logging
import math
import os
from typing import Any, Callable, Dict, Mapping, Optional

logger = logging.getLogger(__name__)


class EnvironmentMismatchError(RuntimeError):
    """Raised when key prefix, base URL, or account probe conflicts with environment mode."""
    pass


class TradingEnvironment(str, Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"


PAPER_BASE_URL = "https://paper-api.alpaca.markets"
LIVE_BASE_URL = "https://api.alpaca.markets"

PAPER_KEY_PREFIX = "PK"
LIVE_KEY_PREFIX = "AK"

#: Alpaca ``AccountStatus`` values. ``ACTIVE`` is the normal tradable state;
#: ``PAPER_ONLY`` denotes an account restricted to the paper environment.
ACTIVE_STATUS = "ACTIVE"
PAPER_ONLY_STATUS = "PAPER_ONLY"

#: Observed (not officially documented) prefix on Alpaca paper account numbers.
#: Used only as a *positive paper* signal — never to infer that an account is live.
PAPER_ACCOUNT_NUMBER_PREFIX = "PA"

#: Account flags Alpaca documents as "the account is not allowed to place orders"
#: (``trading_blocked``, ``trade_suspended_by_user``) or as prohibiting account
#: activity entirely (``account_blocked``).
ORDER_BLOCKING_ACCOUNT_FLAGS = (
    "trading_blocked",
    "account_blocked",
    "trade_suspended_by_user",
)

VALID_ORDER_SIDES = ("buy", "sell")

_TRUE_TOKENS = frozenset({"true", "1", "yes", "y", "t"})
_FALSE_TOKENS = frozenset({"false", "0", "no", "n", "f"})


def _as_bool(value: Any) -> Optional[bool]:
    """Interpret a JSON-ish boolean, returning ``None`` when uninterpretable.

    Broker payloads and loosely-typed adapters routinely deliver ``"true"``/
    ``"false"`` as strings. A strict ``is True`` test silently discards those, so
    an explicit *negative* signal (``is_paper: "false"``, ``trading_blocked:
    "true"``) would be read as "absent" and waved through.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _TRUE_TOKENS:
            return True
        if token in _FALSE_TOKENS:
            return False
    return None


def _coerce_environment(value: Any) -> TradingEnvironment:
    """Coerce a configured environment value into a ``TradingEnvironment``.

    Accepts the enum itself or its plain-string form (``"LIVE"``/``"PAPER"``,
    case-insensitive), which is what a YAML/env-sourced config supplies. Anything
    unrecognised raises, so an unknown environment can never fall through the mode
    checks and be treated as authorised.
    """
    if isinstance(value, TradingEnvironment):
        return value
    if isinstance(value, str):
        try:
            return TradingEnvironment(value.strip().upper())
        except ValueError:
            pass
    raise ValueError(
        f"environment must be a TradingEnvironment (or its name); got {value!r}. "
        f"Valid values: {[e.value for e in TradingEnvironment]}."
    )


@dataclass(frozen=True)
class AlpacaConfig:
    """Immutable configuration container — credentials cannot be mutated after validation."""

    environment: TradingEnvironment
    key_id: str
    secret_key: str
    base_url: str

    def __post_init__(self) -> None:
        # Normalise the environment up front so every downstream comparison is
        # against a real enum member rather than an arbitrary object.
        object.__setattr__(self, "environment", _coerce_environment(self.environment))
        if not isinstance(self.key_id, str) or not self.key_id.strip():
            raise ValueError("key_id must not be empty or whitespace.")
        if not isinstance(self.secret_key, str) or not self.secret_key.strip():
            raise ValueError("secret_key must not be empty or whitespace.")
        if not isinstance(self.base_url, str) or not self.base_url.strip():
            raise ValueError("base_url must not be empty or whitespace.")


class AlpacaEnvironmentManager:
    """
    Validates Alpaca API credentials and base URLs, ensuring strict separation
    between paper and live environments before any orders are placed.

    :param allow_live_trading_env_var: name of the environment variable that must
        equal ``"true"`` before LIVE mode is permitted.
    :param require_environment_evidence: when ``True``, ``probe_account`` vetoes if
        the account response carries no signal identifying the environment. The
        default (``False``) is correct for the real Alpaca REST API, whose account
        payload contains no such field; enable it only when the supplied
        ``get_account_fn`` is known to provide one.
    """

    def __init__(
        self,
        allow_live_trading_env_var: str = "ALLOW_LIVE_TRADING",
        require_environment_evidence: bool = False,
    ):
        self.allow_live_env_var = allow_live_trading_env_var
        self.require_environment_evidence = require_environment_evidence

    def validate_config(self, config: AlpacaConfig) -> bool:
        """Validates key prefix, base URL matching, and live safety flags.

        The base URL must match the expected endpoint exactly. The key-prefix rule
        only *rejects* a credential carrying the opposite environment's prefix, so
        an unrecognised key format is neither silently blessed nor wrongly refused.
        """
        env = _coerce_environment(config.environment)
        key_id = config.key_id.strip()
        # Hostnames are case-insensitive; casefold before matching the exact
        # allow-list so `HTTPS://API.alpaca.markets` is not a spurious veto. This
        # cannot admit any host beyond the two constants below.
        base_url = config.base_url.strip().rstrip("/").casefold()

        if env == TradingEnvironment.PAPER:
            if base_url != PAPER_BASE_URL:
                raise EnvironmentMismatchError(
                    f"CRITICAL SAFETY BREACH: Paper mode configured but base_url does not match "
                    f"PAPER endpoint ({PAPER_BASE_URL}). Got: {base_url}"
                )
            if key_id.upper().startswith(LIVE_KEY_PREFIX):
                raise EnvironmentMismatchError(
                    "Key ID starts with 'AK' (live format) while operating in PAPER mode. "
                    "Cannot use live credentials for a paper environment."
                )

        elif env == TradingEnvironment.LIVE:
            if base_url != LIVE_BASE_URL:
                raise EnvironmentMismatchError(
                    f"LIVE mode configured but base_url does not match LIVE endpoint ({LIVE_BASE_URL}). "
                    f"Got: {base_url}"
                )
            if key_id.upper().startswith(PAPER_KEY_PREFIX):
                raise EnvironmentMismatchError(
                    "LIVE mode configured but key_id starts with 'PK' (paper format)!"
                )

            allow_live = os.environ.get(self.allow_live_env_var, "").strip().lower() == "true"
            if not allow_live:
                raise EnvironmentMismatchError(
                    f"LIVE trading blocked! Environment variable '{self.allow_live_env_var}=true' is required."
                )
            logger.warning(
                "LIVE trading authorized via %s=true - real capital is at risk.",
                self.allow_live_env_var,
            )

        else:  # pragma: no cover - defensive; _coerce_environment admits nothing else
            raise EnvironmentMismatchError(
                f"Unrecognised trading environment {env!r}; refusing to authorise."
            )

        logger.info(
            "Alpaca configuration validated successfully for %s mode at %s.", env.value, base_url
        )
        return True

    @staticmethod
    def resolve_account_environment(
        account_data: Mapping[str, Any]
    ) -> Optional[TradingEnvironment]:
        """Best-effort identification of the environment an account belongs to.

        Returns ``None`` when the payload carries no usable signal - which is the
        normal case for Alpaca's REST response. Signals, in precedence order:

        1. ``is_paper`` - not returned by the REST API, but supplied by some SDK
           wrappers and adapters; authoritative whenever it is interpretable as a
           boolean (including the string forms ``"true"``/``"false"``).
        2. ``status == "PAPER_ONLY"`` - a documented Alpaca ``AccountStatus``.
        3. ``account_number`` beginning ``PA`` - an observed, unofficial paper
           convention, used only to identify paper, never to assert live.
        """
        is_paper = _as_bool(account_data.get("is_paper"))
        if is_paper is not None:
            return TradingEnvironment.PAPER if is_paper else TradingEnvironment.LIVE

        status = str(account_data.get("status") or "").strip().upper()
        if status == PAPER_ONLY_STATUS:
            return TradingEnvironment.PAPER

        account_number = str(account_data.get("account_number") or "").strip().upper()
        if account_number.startswith(PAPER_ACCOUNT_NUMBER_PREFIX):
            return TradingEnvironment.PAPER

        return None

    def probe_account(
        self, config: AlpacaConfig, get_account_fn: Callable[[], Dict[str, Any]]
    ) -> bool:
        """
        Probes the Alpaca GET /v2/account endpoint and verifies the account is
        tradable and consistent with the configured environment.

        Checks performed: the account status is tradable, no order-blocking flag is
        set, and any resolvable environment signal matches the configured mode.

        Alpaca's account payload has no ``is_paper`` field, so an inability to
        resolve the environment is reported as *undeterminable* and logged, not
        treated as live - treating it as live would veto every legitimate paper
        deployment. Set ``require_environment_evidence=True`` to veto instead.
        """
        self.validate_config(config)
        env = _coerce_environment(config.environment)

        try:
            account_data = get_account_fn()
        except Exception as e:
            raise EnvironmentMismatchError(
                f"Failed to probe Alpaca account endpoint: {e}"
            ) from e

        if not isinstance(account_data, Mapping):
            raise EnvironmentMismatchError(
                f"Alpaca account probe returned {type(account_data).__name__}, expected a mapping."
            )

        # A missing status is *not* assumed healthy - an unreadable account is a veto.
        raw_status = account_data.get("status")
        status = str(raw_status).strip().upper() if raw_status is not None else ""
        if not status:
            raise EnvironmentMismatchError(
                "Alpaca account probe response carries no 'status' field; refusing to authorise."
            )

        # PAPER_ONLY is tradable, but only in paper mode: seeing it in LIVE mode
        # means the configuration is pointed at an account that cannot trade live.
        allowed_statuses = (
            (ACTIVE_STATUS, PAPER_ONLY_STATUS)
            if env == TradingEnvironment.PAPER
            else (ACTIVE_STATUS,)
        )
        if status not in allowed_statuses:
            raise EnvironmentMismatchError(
                f"Alpaca account is not tradable in {env.value} mode (status='{status}')."
            )

        # A flag that is present but not interpretable as False fails closed: an
        # unreadable blocking flag is treated as set, not as absent.
        blocked = []
        for flag in ORDER_BLOCKING_ACCOUNT_FLAGS:
            raw_flag = account_data.get(flag)
            if raw_flag is not None and _as_bool(raw_flag) is not False:
                blocked.append(flag)
        if blocked:
            raise EnvironmentMismatchError(
                f"Alpaca account is blocked from placing orders (flags set: {', '.join(blocked)})."
            )

        # An is_paper that is present but unreadable is a corrupt discriminator,
        # not an absent one — refuse rather than silently falling back.
        raw_is_paper = account_data.get("is_paper")
        if raw_is_paper is not None and _as_bool(raw_is_paper) is None:
            raise EnvironmentMismatchError(
                f"Account probe returned an uninterpretable 'is_paper' value "
                f"({raw_is_paper!r}); refusing to authorise."
            )

        resolved = self.resolve_account_environment(account_data)
        if resolved is None:
            if self.require_environment_evidence:
                raise EnvironmentMismatchError(
                    "Account environment could not be determined from the probe response and "
                    "require_environment_evidence=True; refusing to authorise."
                )
            logger.warning(
                "Alpaca account probe carries no environment discriminator "
                "(GET /v2/account returns no 'is_paper' field); environment separation "
                "rests on the verified base URL %s.",
                config.base_url.strip().rstrip("/"),
            )
        elif resolved != env:
            if env == TradingEnvironment.PAPER:
                raise EnvironmentMismatchError(
                    "CRITICAL MISMATCH: Bot configured for PAPER mode, but probed account "
                    "is a LIVE real-money account!"
                )
            raise EnvironmentMismatchError(
                "MISMATCH: Bot configured for LIVE mode, but probed account is a PAPER account!"
            )

        logger.info(
            "Account probe verified: status='%s', resolved_environment=%s.",
            status,
            resolved.value if resolved else "UNDETERMINED",
        )
        return True

    @staticmethod
    def _validate_order_parameters(symbol: str, qty: float, side: str) -> None:
        """Rejects structurally invalid orders before they reach the broker."""
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol must be a non-empty string.")
        # bool is a subclass of int; True would otherwise slip through as qty=1.
        if isinstance(qty, bool) or not isinstance(qty, (int, float)):
            raise ValueError(f"qty must be a real number; got {qty!r}.")
        if not math.isfinite(qty) or qty <= 0:
            raise ValueError(f"qty must be finite and greater than zero; got {qty!r}.")
        if not isinstance(side, str) or side.strip().lower() not in VALID_ORDER_SIDES:
            raise ValueError(f"side must be one of {VALID_ORDER_SIDES}; got {side!r}.")

    def guard_order(
        self,
        config: AlpacaConfig,
        symbol: str,
        qty: float,
        side: str,
        get_account_fn: Optional[Callable[[], Dict[str, Any]]] = None,
    ) -> bool:
        """
        Veto gate executed before submitting any order to Alpaca.

        Validates the configuration and the order parameters, and - when
        ``get_account_fn`` is supplied - re-probes the account, providing a runtime
        defence against environment drift after initialisation.
        """
        self.validate_config(config)
        self._validate_order_parameters(symbol, qty, side)

        if get_account_fn:
            self.probe_account(config, get_account_fn)

        logger.info(
            "Order submission authorized for %s: %s %s '%s'",
            _coerce_environment(config.environment).value,
            side,
            qty,
            symbol,
        )
        return True
