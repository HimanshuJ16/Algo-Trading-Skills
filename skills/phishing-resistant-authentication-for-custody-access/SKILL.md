---
name: phishing-resistant-authentication-for-custody-access
description: >-
  Phishing-resistant authentication engine verifying FIDO2/WebAuthn hardware security keys, cryptographic origin binding, and user verification flags for institutional crypto custody access.
domain: Crypto Custody & System Security
subdomain: Authentication & Access Control
tags: ["webauthn", "fido2", "phishing-resistant", "crypto-custody", "hardware-security-key", "yubikey", "origin-binding"]
brokers_frameworks: ["W3C WebAuthn Level 3", "FIDO2 CTAP2 Standard", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when implementing authentication and authorization gateways for institutional crypto custody systems, hot wallet administration portals, or signing key operations. Traditional Multi-Factor Authentication (SMS 2FA, Email OTP, TOTP apps) is vulnerable to Adversary-in-the-Middle (AiTM) reverse-proxy phishing attacks (e.g., Evilginx2). FIDO2 / WebAuthn hardware security keys (YubiKey, Titan Key, Passkeys) enforce cryptographic origin binding, making it mathematically impossible for phished proxy domains to authenticate against custody APIs.

## Prerequisites

- WebAuthn authentication assertion (`user_id`, `client_origin`, `rp_id`, `challenge`, `user_present`, `user_verified`).
- Configured auth security policy (`allowed_rp_ids`, `require_user_verification`, `max_challenge_age_sec`).

## Workflow

1. **Cryptographic Origin Binding Check**:
   - Extract `client_origin` from `clientDataJSON`.
   - Verify origin matches the expected Relying Party ID (`https://{rp_id}`).
   - Reject authentication if origin mismatch occurs (detecting AiTM proxy clone).
2. **User Presence & Verification Audit**:
   - Check `user_present` flag (`UP=1` indicating physical button touch).
   - Check `user_verified` flag (`UV=1` indicating PIN or biometric verification).
3. **Challenge Expiration & Signature Check**:
   - Validate cryptographic assertion challenge freshness.
4. **Audit Report Generation**: Output structured `AuthVerificationReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Accepting Weak MFA Fallbacks**: Allowing SMS OTP or TOTP as backup options for high-privilege custody administrative actions, neutralizing WebAuthn phishing resistance.
- **Ignoring Origin Binding Verification**: Verifying WebAuthn signatures without checking `clientDataJSON.origin`, allowing phished domains to proxy valid signatures.
- **Disabling User Verification (UV)**: Accepting assertions without requiring PIN or biometric verification (`UV=0`), allowing unauthorized physical access to unattended hardware keys.

## Verification

- Instantiate `PhishingResistantAuthenticationForCustodyAccessEngine`. Submit valid WebAuthn assertion from `https://custody.firm.com` with `UV=1, UP=1` $\implies$ verify `AUTH_SUCCESSFUL`. Submit phished domain `https://cust0dy-firm.com` $\implies$ verify `ORIGIN_MISMATCH_PHISHING_ATTEMPT` rejection.
- Run `python scripts/test_phishing_resistant_authentication_for_custody_access.py`.

## Related Skills

- `hardware-security-module-hsm-for-signing-keys`
- `multi-party-computation-mpc-custody-solutions`
---
