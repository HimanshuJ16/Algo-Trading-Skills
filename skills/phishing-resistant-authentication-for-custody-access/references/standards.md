# Standards for Phishing-Resistant Authentication for Custody Access

| Metric | Engineering Standard |
|---|---|
| Protocol Standard | W3C WebAuthn Level 3 / FIDO2 CTAP2. |
| Origin Requirement | Origin MUST strictly match `https://{rp_id}` scheme and domain. |
| Verification Flags | Both `user_present` (UP) AND `user_verified` (UV) MUST be True. |