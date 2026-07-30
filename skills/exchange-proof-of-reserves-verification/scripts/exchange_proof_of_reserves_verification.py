import hashlib
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class UserAccountBalance:
    account_id: str
    asset_symbol: str                   # e.g. 'BTC', 'ETH', 'USDT'
    balance: float

@dataclass
class MerkleAuditPathNode:
    sibling_hash: str
    sibling_balance: float
    is_sibling_right: bool

@dataclass
class ProofOfReservesAuditReport:
    exchange_name: str
    asset_symbol: str
    declared_merkle_root_hash: str
    computed_merkle_root_hash: str
    is_user_inclusion_verified: bool
    total_declared_liabilities: float
    total_verified_onchain_reserves: float
    reserve_ratio_percentage: float     # (OnChain / Liabilities) * 100.0
    solvency_status: str                # 'SOLVENT_FULL_RESERVES', 'INSOLVENT_RESERVE_DEFICIT', 'INVALID_MERKLE_PROOF'
    audit_notes: str

class ExchangeProofOfReservesEngine:
    """
    Quantitative crypto custody and audit engine for verifying exchange Merkle Sum Tree balance inclusion proofs,
    validating on-chain reserve ratios (>= 100%), and detecting solvency deficits.
    """
    def __init__(self, min_reserve_ratio_pct: float = 100.0):
        self.min_reserve_ratio_pct = min_reserve_ratio_pct

    def compute_leaf_hash(self, account_id: str, asset_symbol: str, balance: float) -> str:
        data = f"{account_id}:{asset_symbol}:{balance:.8f}".encode("utf-8")
        return hashlib.sha256(data).hexdigest()

    def compute_parent_hash(self, left_hash: str, left_bal: float, right_hash: str, right_bal: float) -> Tuple[str, float]:
        parent_bal = round(left_bal + right_bal, 8)
        data = f"{left_hash}:{left_bal:.8f}:{right_hash}:{right_bal:.8f}".encode("utf-8")
        return hashlib.sha256(data).hexdigest(), parent_bal

    def verify_proof_of_reserves(
        self,
        exchange_name: str,
        user_leaf: UserAccountBalance,
        audit_path: List[MerkleAuditPathNode],
        declared_merkle_root: str,
        total_declared_liabilities: float,
        total_verified_onchain_reserves: float
    ) -> ProofOfReservesAuditReport:
        """
        Verifies Merkle tree inclusion path, audits negative balances, and calculates on-chain reserve ratio.
        """
        # Rule 1: Negative Balance Audit
        if user_leaf.balance < 0:
            msg = f"MERKLE MANIPULATION DETECTED [{exchange_name}]: User '{user_leaf.account_id}' has negative balance {user_leaf.balance:.8f}!"
            logger.critical(msg)
            return ProofOfReservesAuditReport(
                exchange_name=exchange_name, asset_symbol=user_leaf.asset_symbol,
                declared_merkle_root_hash=declared_merkle_root, computed_merkle_root_hash="INVALID",
                is_user_inclusion_verified=False, total_declared_liabilities=total_declared_liabilities,
                total_verified_onchain_reserves=total_verified_onchain_reserves, reserve_ratio_percentage=0.0,
                solvency_status="INVALID_MERKLE_PROOF", audit_notes=msg
            )

        # Rule 2: Reconstruct Merkle Root Hash along Audit Path
        curr_hash = self.compute_leaf_hash(user_leaf.account_id, user_leaf.asset_symbol, user_leaf.balance)
        curr_bal = user_leaf.balance

        for node in audit_path:
            if node.sibling_balance < 0:
                msg = f"MERKLE MANIPULATION DETECTED [{exchange_name}]: Sibling node has negative balance {node.sibling_balance:.8f}!"
                logger.critical(msg)
                return ProofOfReservesAuditReport(
                    exchange_name=exchange_name, asset_symbol=user_leaf.asset_symbol,
                    declared_merkle_root_hash=declared_merkle_root, computed_merkle_root_hash="INVALID",
                    is_user_inclusion_verified=False, total_declared_liabilities=total_declared_liabilities,
                    total_verified_onchain_reserves=total_verified_onchain_reserves, reserve_ratio_percentage=0.0,
                    solvency_status="INVALID_MERKLE_PROOF", audit_notes=msg
                )

            if node.is_sibling_right:
                curr_hash, curr_bal = self.compute_parent_hash(curr_hash, curr_bal, node.sibling_hash, node.sibling_balance)
            else:
                curr_hash, curr_bal = self.compute_parent_hash(node.sibling_hash, node.sibling_balance, curr_hash, curr_bal)

        is_inclusion_valid = (curr_hash == declared_merkle_root)

        # Rule 3: On-Chain Solvency Reserve Ratio Audit
        if total_declared_liabilities <= 0:
            raise ValueError("Total declared liabilities must be > 0.")

        reserve_ratio = round((total_verified_onchain_reserves / float(total_declared_liabilities)) * 100.0, 2)

        if not is_inclusion_valid:
            status = "INVALID_MERKLE_PROOF"
            notes = f"PROOF REJECTED [{exchange_name}]: Computed Merkle Root '{curr_hash[:12]}...' does not match declared root '{declared_merkle_root[:12]}...'."
            logger.error(notes)
        elif reserve_ratio < self.min_reserve_ratio_pct:
            status = "INSOLVENT_RESERVE_DEFICIT"
            notes = (
                f"INSOLVENT DEFICIT DETECTED [{exchange_name}]: On-chain reserves ({total_verified_onchain_reserves:,.2f} {user_leaf.asset_symbol}) "
                f"cover only {reserve_ratio:.2f}% of liabilities ({total_declared_liabilities:,.2f} {user_leaf.asset_symbol}). Target: {self.min_reserve_ratio_pct}%."
            )
            logger.critical(notes)
        else:
            status = "SOLVENT_FULL_RESERVES"
            notes = (
                f"PROOF OF RESERVES VERIFIED [{exchange_name}]: User inclusion verified. "
                f"On-chain reserves cover {reserve_ratio:.2f}% of user liabilities ({total_verified_onchain_reserves:,.2f} / {total_declared_liabilities:,.2f} {user_leaf.asset_symbol})."
            )
            logger.info(notes)

        return ProofOfReservesAuditReport(
            exchange_name=exchange_name,
            asset_symbol=user_leaf.asset_symbol,
            declared_merkle_root_hash=declared_merkle_root,
            computed_merkle_root_hash=curr_hash,
            is_user_inclusion_verified=is_inclusion_valid,
            total_declared_liabilities=total_declared_liabilities,
            total_verified_onchain_reserves=total_verified_onchain_reserves,
            reserve_ratio_percentage=reserve_ratio,
            solvency_status=status,
            audit_notes=notes
        )
