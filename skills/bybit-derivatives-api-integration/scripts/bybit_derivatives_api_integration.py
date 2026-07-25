import hmac
import hashlib
import time
import json
from dataclasses import dataclass
from typing import Dict, Any, Optional

@dataclass
class BybitConfig:
    api_key: str
    api_secret: str
    is_testnet: bool = True
    recv_window: int = 5000

class BybitV5Authenticator:
    """
    Handles HMAC-SHA256 signature generation and payload construction 
    for Bybit V5 Quantitative Trading integrations.
    """
    def __init__(self, config: BybitConfig):
        self.config = config
        self.base_url = "https://api-testnet.bybit.com" if config.is_testnet else "https://api.bybit.com"

    def _generate_signature(self, timestamp: str, payload: str) -> str:
        """
        Generates HMAC-SHA256 signature.
        param_str = timestamp + api_key + recv_window + (queryString | jsonBodyString)
        """
        param_str = f"{timestamp}{self.config.api_key}{self.config.recv_window}{payload}"
        return hmac.new(
            bytes(self.config.api_secret, "utf-8"),
            param_str.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def sign_request(self, method: str, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Constructs the fully signed headers and payload required for a Bybit V5 request.
        """
        timestamp = str(int(time.time() * 1000))
        params = params or {}
        
        if method.upper() == "GET":
            # Sort parameters alphabetically and urlencode
            sorted_params = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
            signature = self._generate_signature(timestamp, sorted_params)
            body_or_query = sorted_params
        elif method.upper() == "POST":
            # Dump JSON with no whitespace
            body_or_query = json.dumps(params, separators=(",", ":")) if params else ""
            signature = self._generate_signature(timestamp, body_or_query)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        headers = {
            "X-BAPI-API-KEY": self.config.api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": str(self.config.recv_window),
            "Content-Type": "application/json"
        }

        return {
            "url": f"{self.base_url}{endpoint}",
            "headers": headers,
            "payload": body_or_query
        }
