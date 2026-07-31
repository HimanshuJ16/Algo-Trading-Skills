import hmac
import hashlib
import base64
import time
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

@dataclass
class KrakenWsTokenState:
    token: str
    created_timestamp_epoch: float
    expires_in_seconds: float = 900.0   # 15 minutes validity
    is_active: bool = True

@dataclass
class KrakenWsV2SubscriptionSpec:
    channel: str                        # 'book', 'ticker', 'trade', 'executions', 'balances'
    symbols: Optional[List[str]] = None # e.g. ['BTC/USD', 'ETH/USD']
    depth: Optional[int] = 10           # For orderbook depth
    snap_orders: bool = True
    snap_trades: bool = True

@dataclass
class KrakenWsV2Report:
    channel: str
    is_private_channel: bool
    ws_url: str                         # 'wss://ws.kraken.com/v2' or 'wss://ws-auth.kraken.com/v2'
    subscription_json_frame: Dict[str, Any]
    token_age_seconds: float
    is_token_valid: bool
    status: str                         # 'SUBSCRIPTION_FRAME_CREATED', 'TOKEN_REFRESH_REQUIRED', 'INVALID_CHANNEL'
    audit_notes: str

class KrakenWsV2ManagerEngine:
    """
    Crypto exchange API client for Kraken WebSocket v2 API,
    computing HMAC-SHA512 signatures for REST token retrieval, building public/private v2 subscription frames, and managing 15-minute token lifecycles.
    """
    def __init__(self, api_key: str = "MOCK_KRAKEN_KEY", api_secret_b64: str = "bW9ja19rcmFrZW5fc2VjcmV0X2I2NF9lbmNvZGVk"):
        self.api_key = api_key
        self.api_secret_b64 = api_secret_b64
        self.public_ws_url = "wss://ws.kraken.com/v2"
        self.private_ws_url = "wss://ws-auth.kraken.com/v2"

    def generate_kraken_rest_hmac_signature(
        self,
        url_path: str,
        nonce: str,
        post_data: str
    ) -> str:
        """
        Generates official Kraken REST API HMAC-SHA512 signature for GetWebSocketsToken authentication.
        API-Sign = Base64Encode( HMAC-SHA512( Path + SHA256(nonce + postData), Base64Decode(API-Secret) ) )
        """
        # Step 1: SHA256(nonce + postData)
        message = (nonce + post_data).encode("utf-8")
        sha256_digest = hashlib.sha256(message).digest()

        # Step 2: Path + SHA256_digest
        hmac_msg = url_path.encode("utf-8") + sha256_digest

        # Step 3: Base64 decode API Secret
        try:
            secret_bytes = base64.b64decode(self.api_secret_b64)
        except Exception:
            secret_bytes = self.api_secret_b64.encode("utf-8")

        # Step 4: HMAC-SHA512 signature
        signature = hmac.new(secret_bytes, hmac_msg, hashlib.sha512).digest()

        # Step 5: Base64 encode signature
        return base64.b64encode(signature).decode("utf-8")

    def build_v2_subscription_frame(
        self,
        spec: KrakenWsV2SubscriptionSpec,
        token_state: Optional[KrakenWsTokenState] = None,
        current_time_epoch: Optional[float] = None
    ) -> KrakenWsV2Report:
        """
        Builds official Kraken WebSocket v2 subscription JSON request frame and checks token expiration.
        """
        now = current_time_epoch if current_time_epoch is not None else time.time()
        chan_clean = spec.channel.strip().lower()
        private_channels = {"executions", "balances", "add_order", "cancel_order"}
        is_private = chan_clean in private_channels

        ws_endpoint = self.private_ws_url if is_private else self.public_ws_url

        # Check Token Expiration for Private Channels
        token_age = 0.0
        is_token_ok = True

        if is_private:
            if not token_state or not token_state.token:
                notes = f"KRAKEN WS V2 REJECT [{chan_clean}]: Private channel requires an active WebSocket Token!"
                logger.error(notes)
                return KrakenWsV2Report(
                    channel=chan_clean, is_private_channel=True, ws_url=ws_endpoint,
                    subscription_json_frame={}, token_age_seconds=0.0, is_token_valid=False,
                    status="MISSING_WS_TOKEN", audit_notes=notes
                )

            token_age = round(now - token_state.created_timestamp_epoch, 2)
            # Token expires at 15 mins (900s), flag refresh at 12 mins (720s)
            if token_age >= 720.0:
                is_token_ok = False
                notes = (
                    f"KRAKEN WS V2 REJECT [{chan_clean}]: WS Token age ({token_age:.1f}s) "
                    f"nears 15-minute expiration (720s limit). Token refresh required!"
                )
                logger.warning(notes)
                return KrakenWsV2Report(
                    channel=chan_clean, is_private_channel=True, ws_url=ws_endpoint,
                    subscription_json_frame={}, token_age_seconds=token_age, is_token_valid=False,
                    status="TOKEN_REFRESH_REQUIRED", audit_notes=notes
                )

        # Build v2 Subscription JSON Frame
        params: Dict[str, Any] = {"channel": chan_clean}

        if is_private:
            params["token"] = token_state.token
            if chan_clean == "executions":
                params["snap_orders"] = spec.snap_orders
                params["snap_trades"] = spec.snap_trades
        else:
            if spec.symbols:
                params["symbol"] = spec.symbols
            if chan_clean == "book" and spec.depth:
                params["depth"] = spec.depth

        frame = {
            "method": "subscribe",
            "params": params
        }

        notes = (
            f"KRAKEN WS V2 SUB CREATED [{chan_clean}]: Endpoint = {ws_endpoint}. "
            f"Private = {is_private}. Frame = {json.dumps(frame)}."
        )
        logger.info(notes)

        return KrakenWsV2Report(
            channel=chan_clean,
            is_private_channel=is_private,
            ws_url=ws_endpoint,
            subscription_json_frame=frame,
            token_age_seconds=token_age,
            is_token_valid=is_token_ok,
            status="SUBSCRIPTION_FRAME_CREATED",
            audit_notes=notes
        )
