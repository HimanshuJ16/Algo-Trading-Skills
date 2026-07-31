import base64
import hashlib
import hmac
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class OKXTokenBalance:
    currency: str                       # e.g., 'BTC', 'ETH', 'USDT'
    total_balance: float
    usd_price: float
    discount_factor: float = 1.0         # OKX multi-currency haircut tier (e.g. BTC=1.0, ETH=0.95)

@dataclass
class OKXAccountReport:
    account_mode: str                    # 'multi_currency_margin'
    total_usd_equity: float
    adjusted_usd_equity: float
    maintenance_margin_usd: float
    margin_ratio_pct: float
    status: str                          # 'SAFE', 'MARGIN_WARNING', 'LIQUIDATION_RISK_CALL'
    audit_notes: str

class OKXUnifiedAccountEngine:
    """
    OKX v5 Unified Account API integration managing HMAC-SHA256 authentication,
    multi-currency margin mode equity calculations, margin ratio monitoring, and order payload building.
    """
    def __init__(self, api_key: str, secret_key: str, passphrase: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase

    def generate_signature(
        self, timestamp: str, method: str, request_path: str, body: str = ""
    ) -> str:
        """
        Generates OKX v5 HMAC-SHA256 Base64 signature for private endpoint authentication.
        Prehash format: timestamp + method.upper() + request_path + body
        """
        prehash = timestamp + method.upper() + request_path + body
        mac = hmac.new(
            self.secret_key.encode("utf-8"),
            prehash.encode("utf-8"),
            digestmod=hashlib.sha256
        )
        return base64.b64encode(mac.digest()).decode("utf-8")

    def get_auth_headers(
        self, method: str, request_path: str, body: str = "", timestamp: Optional[str] = None
    ) -> Dict[str, str]:
        """
        Builds mandatory OKX v5 HTTP headers.
        """
        if not timestamp:
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        signature = self.generate_signature(timestamp, method, request_path, body)

        return {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json"
        }

    def compute_multi_currency_margin(
        self, balances: List[OKXTokenBalance], maintenance_margin_usd: float
    ) -> OKXAccountReport:
        """
        Computes total USD equity, discount-factored adjusted equity, and margin ratio (mrr).
        Margin ratio formula: mrr = (adjusted_usd_equity / maintenance_margin_usd) * 100.0
        """
        total_usd_equity = sum(b.total_balance * b.usd_price for b in balances)
        adj_usd_equity = sum(b.total_balance * b.usd_price * b.discount_factor for b in balances)

        if maintenance_margin_usd <= 0:
            mrr_pct = 9999.0
        else:
            mrr_pct = (adj_usd_equity / maintenance_margin_usd) * 100.0

        if mrr_pct > 300.0:
            status = "SAFE"
        elif mrr_pct > 100.0:
            status = "MARGIN_WARNING"
        else:
            status = "LIQUIDATION_RISK_CALL"

        notes = (
            f"OKX UNIFIED ACCOUNT [{status}]: Total USD Equity = ${total_usd_equity:,.2f}, "
            f"Adjusted Equity = ${adj_usd_equity:,.2f}, Maintenance Margin = ${maintenance_margin_usd:,.2f}, "
            f"Margin Ratio = {mrr_pct:.2f}%."
        )
        if status != "SAFE":
            logger.warning(notes)
        else:
            logger.info(notes)

        return OKXAccountReport(
            account_mode="multi_currency_margin",
            total_usd_equity=round(total_usd_equity, 2),
            adjusted_usd_equity=round(adj_usd_equity, 2),
            maintenance_margin_usd=round(maintenance_margin_usd, 2),
            margin_ratio_pct=round(mrr_pct, 2),
            status=status,
            audit_notes=notes
        )

    def build_order_payload(
        self,
        inst_id: str,
        td_mode: str,
        side: str,
        ord_type: str,
        size: float,
        price: Optional[float] = None
    ) -> Dict[str, str]:
        """
        Constructs OKX /api/v5/trade/order REST JSON payload.
        """
        payload = {
            "instId": inst_id,
            "tdMode": td_mode.lower(),            # 'cross', 'isolated'
            "side": side.lower(),                # 'buy', 'sell'
            "ordType": ord_type.lower(),          # 'limit', 'market'
            "sz": str(size)
        }
        if price is not None:
            payload["px"] = str(price)
        return payload
