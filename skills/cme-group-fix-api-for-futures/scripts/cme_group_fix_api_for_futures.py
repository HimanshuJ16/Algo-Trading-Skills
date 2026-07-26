import logging
from dataclasses import dataclass
from typing import Optional, Dict

logger = logging.getLogger(__name__)

@dataclass
class CmeFixOrderParams:
    symbol: str
    side: str                          # '1' (Buy) or '2' (Sell)
    quantity: int
    price: float
    cl_ord_id: str
    operator_id: str                   # Tag 50
    smp_id: str                        # Tag 7928 (SelfMatchPreventionID)
    smp_instruction: str = "O"         # Tag 8000 ('O' = Cancel Outgoing, 'R' = Cancel Resting)
    is_automated: bool = True          # Tag 1028 ('N' for automated, 'Y' for manual)
    account: str = "HFT_ACCT"

class CmeFixSessionEngine:
    """
    CME Group FIX 4.2/4.4 Session & Order Engine.
    Handles sequence number tracking (Tag 34), gap-fill recovery, Tag 1028 (ManualOrderIndicator),
    and Tag 7928/8000 Self-Match Prevention (SMP) formatting.
    """
    def __init__(self, sender_comp_id: str, target_comp_id: str = "CME"):
        self.sender_comp_id = sender_comp_id
        self.target_comp_id = target_comp_id
        self.outbound_seq_num = 1
        self.expected_inbound_seq_num = 1

    def build_fix_message(self, msg_type: str, body_tags: Dict[int, str]) -> str:
        """
        Formats a standard FIX message with header, body tags, and seq num.
        """
        # Standard Header Tags: 35=MsgType, 49=Sender, 56=Target, 34=SeqNum
        header_parts = [
            f"35={msg_type}",
            f"34={self.outbound_seq_num}",
            f"49={self.sender_comp_id}",
            f"56={self.target_comp_id}"
        ]
        
        body_parts = [f"{tag}={val}" for tag, val in body_tags.items()]
        
        full_msg = "|".join(header_parts + body_parts) + "|"
        self.outbound_seq_num += 1
        return full_msg

    def create_new_order_single(self, params: CmeFixOrderParams) -> str:
        """
        Builds a CME FIX NewOrderSingle (35=D) message with mandatory CME tags.
        """
        if not params.operator_id or len(params.operator_id) < 2:
            raise ValueError("Tag 50 Operator ID must be provided and >= 2 chars.")

        body_tags = {
            11: params.cl_ord_id,
            50: params.operator_id,
            55: params.symbol,
            54: params.side,
            38: str(params.quantity),
            44: f"{params.price:.4f}",
            40: "2",                                        # Limit order
            1028: "N" if params.is_automated else "Y",     # Tag 1028 ManualOrderIndicator
            7928: params.smp_id,                           # Tag 7928 SelfMatchPreventionID
            8000: params.smp_instruction,                 # Tag 8000 SMP Instruction
            1: params.account
        }
        
        return self.build_fix_message("D", body_tags)

    def process_inbound_message(self, msg_parts: Dict[int, str]) -> Optional[str]:
        """
        Processes an incoming FIX message, verifying Tag 34 (MsgSeqNum).
        If a gap is detected, returns a ResendRequest (35=2) message string.
        """
        if 34 not in msg_parts:
            raise ValueError("Inbound FIX message missing Tag 34 (MsgSeqNum).")

        inbound_seq = int(msg_parts[34])
        msg_type = msg_parts.get(35, "")

        if inbound_seq > self.expected_inbound_seq_num:
            # Sequence Gap Detected! Send ResendRequest (35=2)
            logger.warning(
                f"Sequence Gap Detected: Expected {self.expected_inbound_seq_num}, Got {inbound_seq}. Emitting ResendRequest."
            )
            resend_tags = {
                7: str(self.expected_inbound_seq_num), # BeginSeqNo
                16: str(inbound_seq - 1)                # EndSeqNo
            }
            return self.build_fix_message("2", resend_tags)
            
        elif inbound_seq == self.expected_inbound_seq_num:
            self.expected_inbound_seq_num += 1
            return None
        else:
            # Duplicate / Old message
            logger.debug(f"Received old sequence number {inbound_seq} (Expected {self.expected_inbound_seq_num}).")
            return None
