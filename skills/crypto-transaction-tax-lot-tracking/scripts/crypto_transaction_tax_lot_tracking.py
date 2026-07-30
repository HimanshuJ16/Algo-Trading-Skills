import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class CryptoTaxLot:
    lot_id: str
    asset: str                         # e.g. 'ETH', 'BTC', 'SOL'
    acquisition_timestamp: str        # YYYY-MM-DD HH:MM:SS
    days_held: int
    quantity: float
    unit_cost_basis_usd: float

@dataclass
class CryptoDispositionResult:
    disposal_id: str
    asset_sold: str
    asset_received: str
    quantity_disposed: float
    gross_proceeds_usd: float
    gas_fee_usd: float
    net_proceeds_usd: float
    total_cost_basis_usd: float
    realized_gain_loss_usd: float
    is_short_term: bool
    matching_method_used: str

class CryptoTaxLotTrackerEngine:
    """
    Crypto tax accounting engine for tracking tax lots across crypto-to-crypto swaps,
    DEX trades, gas fee deductions, and enforcing HIFO/FIFO tax optimization.
    """
    def __init__(self, default_matching_method: str = "HIFO"):
        self.default_matching_method = default_matching_method
        self.tax_lots: Dict[str, List[CryptoTaxLot]] = {}

    def register_acquisition(self, lot: CryptoTaxLot):
        asset = lot.asset.upper()
        if asset not in self.tax_lots:
            self.tax_lots[asset] = []
        self.tax_lots[asset].append(lot)

    def process_crypto_disposition(
        self,
        disposal_id: str,
        asset_sold: str,
        quantity_sold: float,
        asset_received: str,
        gross_proceeds_usd: float,
        gas_fee_usd: float = 0.0,
        matching_method: Optional[str] = None
    ) -> CryptoDispositionResult:
        """
        Processes a crypto disposal (crypto-to-crypto swap or crypto-to-fiat sale) using HIFO/FIFO/LIFO matching:
        Net Proceeds = Gross Proceeds - Gas Fee
        Realized PnL = Net Proceeds - Cost Basis
        """
        asset = asset_sold.upper()
        if asset not in self.tax_lots or not self.tax_lots[asset]:
            raise ValueError(f"No available tax lots for asset {asset}")

        method = matching_method or self.default_matching_method
        lots = [l for l in self.tax_lots[asset] if l.quantity > 0]

        if not lots:
            raise ValueError(f"No positive quantity tax lots for asset {asset}")

        # Sort Lots by matching method
        if method == "HIFO":
            # Highest cost basis first
            sorted_lots = sorted(lots, key=lambda l: l.unit_cost_basis_usd, reverse=True)
        elif method == "LIFO":
            # Newest acquisition first
            sorted_lots = sorted(lots, key=lambda l: l.acquisition_timestamp, reverse=True)
        else: # FIFO
            # Oldest acquisition first
            sorted_lots = sorted(lots, key=lambda l: l.acquisition_timestamp)

        remaining_qty = quantity_sold
        total_cost_basis = 0.0
        is_all_short_term = True

        for lot in sorted_lots:
            if remaining_qty <= 0:
                break
            
            take_qty = min(remaining_qty, lot.quantity)
            lot_cost = take_qty * lot.unit_cost_basis_usd
            total_cost_basis += lot_cost
            
            if lot.days_held > 365:
                is_all_short_term = False

            lot.quantity -= take_qty
            remaining_qty -= take_qty

        if remaining_qty > 1e-6:
            raise ValueError(f"Insufficient tax lot inventory for {asset}: Short by {remaining_qty:.4f}")

        net_proceeds = gross_proceeds_usd - gas_fee_usd
        realized_pnl = net_proceeds - total_cost_basis

        logger.info(
            f"CRYPTO DISPOSAL [{disposal_id}]: Swapped {quantity_sold} {asset} -> {asset_received}. "
            f"Net Proceeds=${net_proceeds:,.2f}, Cost Basis=${total_cost_basis:,.2f}, PnL=${realized_pnl:,.2f} ({method})"
        )

        return CryptoDispositionResult(
            disposal_id=disposal_id,
            asset_sold=asset,
            asset_received=asset_received,
            quantity_disposed=quantity_sold,
            gross_proceeds_usd=round(gross_proceeds_usd, 2),
            gas_fee_usd=round(gas_fee_usd, 2),
            net_proceeds_usd=round(net_proceeds, 2),
            total_cost_basis_usd=round(total_cost_basis, 2),
            realized_gain_loss_usd=round(realized_pnl, 2),
            is_short_term=is_all_short_term,
            matching_method_used=method
        )
