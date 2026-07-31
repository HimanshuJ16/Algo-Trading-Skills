import re
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class SecurityMasterRecord:
    isin: str                           # 12-char ISIN e.g. 'US0378331005'
    cusip: str                          # 9-char CUSIP e.g. '037833100'
    sedol: str                          # 7-char SEDOL e.g. '2046251'
    figi: str                           # 12-char OpenFIGI e.g. 'BBG000B9XRY4'
    ticker_symbol: str                  # e.g. 'AAPL'
    asset_name: str                     # e.g. 'Apple Inc'
    country_code: str                   # e.g. 'US'

@dataclass
class IdentifierCrossReferenceReport:
    query_identifier: str
    query_type: str                     # 'ISIN', 'CUSIP', 'SEDOL', 'FIGI', 'TICKER'
    is_checksum_valid: bool
    matched_record: Optional[SecurityMasterRecord]
    status: str                         # 'MATCH_FOUND', 'INVALID_CHECKSUM', 'IDENTIFIER_NOT_FOUND'
    audit_notes: str

class IsinCusipSedolCrossReferenceEngine:
    """
    Security master cross-reference service implementing Modulo 10 Luhn checksum algorithms
    for ISIN, CUSIP, and SEDOL identifiers, resolving multi-vendor mappings to immutable OpenFIGI keys.
    """
    def __init__(self):
        # Mock Security Master Database Table
        self.master_db: List[SecurityMasterRecord] = [
            SecurityMasterRecord(
                isin="US0378331005", cusip="037833100", sedol="2046251",
                figi="BBG000B9XRY4", ticker_symbol="AAPL", asset_name="Apple Inc", country_code="US"
            ),
            SecurityMasterRecord(
                isin="US5949181045", cusip="594918104", sedol="2588173",
                figi="BBG000BPH459", ticker_symbol="MSFT", asset_name="Microsoft Corp", country_code="US"
            ),
            SecurityMasterRecord(
                isin="US30303M1027", cusip="30303M102", sedol="BNPYS71",
                figi="BBG000MM82B1", ticker_symbol="META", asset_name="Meta Platforms Inc", country_code="US"
            )
        ]

    def validate_isin_checksum(self, isin: str) -> bool:
        """
        Validates 12-character ISIN Modulo 10 Luhn checksum.
        """
        clean = isin.strip().upper()
        if not re.match(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$", clean):
            return False

        # Convert letters to digits (A=10, ..., Z=35)
        digits_str = ""
        for char in clean:
            if char.isalpha():
                digits_str += str(ord(char) - 55)
            else:
                digits_str += char

        # Luhn Modulo 10 algorithm from right to left
        digits = [int(c) for c in digits_str]
        checksum = 0
        reverse_digits = digits[::-1]

        for idx, digit in enumerate(reverse_digits):
            if idx % 2 == 1: # Double alternate digits
                doubled = digit * 2
                checksum += (doubled // 10) + (doubled % 10)
            else:
                checksum += digit

        return (checksum % 10 == 0)

    def validate_cusip_checksum(self, cusip: str) -> bool:
        """
        Validates 9-character CUSIP Modulo 10 Double-Add-Double checksum.
        """
        clean = cusip.strip().upper()
        if not re.match(r"^[A-Z0-9]{9}$", clean):
            return False

        sum_val = 0
        for idx in range(8):
            char = clean[idx]
            val = ord(char) - 55 if char.isalpha() else int(char)
            if idx % 2 == 1: # Double odd positions (0-indexed 1, 3, 5, 7)
                val *= 2
            sum_val += (val // 10) + (val % 10)

        calc_check_digit = (10 - (sum_val % 10)) % 10
        actual_check_digit = int(clean[8])

        return calc_check_digit == actual_check_digit

    def validate_sedol_checksum(self, sedol: str) -> bool:
        """
        Validates 7-character SEDOL weighted Modulo 10 checksum (Weights: 1, 3, 1, 7, 3, 9, 1).
        """
        clean = sedol.strip().upper()
        if not re.match(r"^[B-Z0-9]{6}[0-9]$", clean): # SEDOL vowels excluded
            return False

        weights = [1, 3, 1, 7, 3, 9]
        sum_val = 0

        for idx in range(6):
            char = clean[idx]
            val = ord(char) - 55 if char.isalpha() else int(char)
            sum_val += val * weights[idx]

        calc_check_digit = (10 - (sum_val % 10)) % 10
        actual_check_digit = int(clean[6])

        return calc_check_digit == actual_check_digit

    def lookup_identifier(self, query: str) -> IdentifierCrossReferenceReport:
        """
        Resolves query identifier across Security Master, performing checksum validation for ISIN, CUSIP, and SEDOL.
        """
        clean_q = query.strip().upper()
        q_type = "UNKNOWN"
        is_chk_valid = True

        if len(clean_q) == 12 and clean_q[:2].isalpha() and clean_q[2:].isalnum():
            q_type = "ISIN"
            is_chk_valid = self.validate_isin_checksum(clean_q)
        elif len(clean_q) == 9 and clean_q.isalnum():
            q_type = "CUSIP"
            is_chk_valid = self.validate_cusip_checksum(clean_q)
        elif len(clean_q) == 7 and clean_q.isalnum():
            q_type = "SEDOL"
            is_chk_valid = self.validate_sedol_checksum(clean_q)
        elif len(clean_q) == 12 and clean_q.startswith("BBG"):
            q_type = "FIGI"
        else:
            q_type = "TICKER"

        if not is_chk_valid:
            notes = f"SECURITY MASTER REJECT [{clean_q}]: Invalid {q_type} checksum."
            logger.error(notes)
            return IdentifierCrossReferenceReport(
                query_identifier=clean_q, query_type=q_type, is_checksum_valid=False,
                matched_record=None, status="INVALID_CHECKSUM", audit_notes=notes
            )

        # Cross-reference search
        matched_rec = None
        for rec in self.master_db:
            if (clean_q in {rec.isin, rec.cusip, rec.sedol, rec.figi, rec.ticker_symbol}):
                matched_rec = rec
                break

        if matched_rec:
            notes = (
                f"SECURITY MASTER MATCH [{clean_q} -> {matched_rec.ticker_symbol}]: "
                f"ISIN = {matched_rec.isin}, CUSIP = {matched_rec.cusip}, SEDOL = {matched_rec.sedol}, FIGI = {matched_rec.figi}."
            )
            logger.info(notes)
            return IdentifierCrossReferenceReport(
                query_identifier=clean_q, query_type=q_type, is_checksum_valid=True,
                matched_record=matched_rec, status="MATCH_FOUND", audit_notes=notes
            )

        notes = f"SECURITY MASTER NOT FOUND [{clean_q}]: Identifier type {q_type} valid but not found in Security Master."
        logger.warning(notes)
        return IdentifierCrossReferenceReport(
            query_identifier=clean_q, query_type=q_type, is_checksum_valid=True,
            matched_record=None, status="IDENTIFIER_NOT_FOUND", audit_notes=notes
        )
