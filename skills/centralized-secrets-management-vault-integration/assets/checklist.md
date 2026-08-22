# Pre-Flight Checklist — Vault Secrets Integration

Run this before a bot that reads live credentials from Vault takes real orders. Anything
unchecked in **Section A** is a stop.

## A. Identity and policy (the actual boundary)

- [ ] The bot authenticates with an **AppRole of its own**, not a root token, not a
      human's personal token, not a role shared with another workload.
- [ ] The AppRole's policy grants read on **only** the paths this bot needs — verified
      against the live token, not read off the policy file: `vault write
      sys/capabilities-self paths=secret/data/prod/<other-venue>/keys` returns `deny`.
- [ ] A cross-environment read is refused **by Vault**, not only by the client: using the
      bot's own token, a read one environment across fails.
- [ ] The policy accounts for wildcard semantics — `path ".../x/*"` does not grant
      `.../x` itself; both are granted if both are read.
- [ ] `secret_id_bound_cidrs` and `token_bound_cidrs` restrict the AppRole to the trading
      hosts' addresses, or there is a written reason why not.

## B. Credential delivery

- [ ] The RoleID and SecretID arrive by **different channels**. No single CI job exports
      both as plaintext environment variables.
- [ ] The SecretID is delivered **response-wrapped**, and the wrapping token's TTL is
      short enough that a stolen wrapper expires before it can be used.
- [ ] A failed unwrap is handled as a **suspected interception incident**, not retried.
- [ ] `secret_id_num_uses` and `secret_id_ttl` are set deliberately, and the behaviour at
      exhaustion is agreed: who delivers the next SecretID, and how fast?

## C. Token lifetime

- [ ] `token_ttl` and `token_max_ttl` are known, and someone has answered: **what happens
      to this bot at max TTL?** (unattended re-login / orchestrator re-delivery /
      periodic token).
- [ ] The bot is not issued a **batch** token — batch tokens cannot be renewed.
- [ ] The bot has been observed surviving a token expiry in staging, with the clock
      advanced or the TTL shortened, rather than assumed to.
- [ ] Re-authentication is **bounded**. A permanently rejected SecretID escalates; it does
      not loop against Vault.

## D. Caching and rotation

- [ ] Every cached secret has an **expiry**. There is no path by which a revoked
      credential can be served indefinitely.
- [ ] The cache TTL is short enough that the worst-case stale-credential window is
      acceptable to whoever performs rotations — and they have been told the number.
- [ ] A rotation signal calls `invalidate()`; the bot does not wait out the TTL when it
      has been told the secret changed.
- [ ] The bot compares `metadata.version` after a refresh, so a rotation that did not take
      effect is visible rather than silent.
- [ ] The behaviour during a **Vault outage** is a deliberate choice (serve stale and keep
      trading, or fail the read), documented and matched to the strategy's risk.

## E. Leakage

- [ ] Printing or logging the loaded configuration object does **not** emit the raw
      secret — verified by actually printing it, not by reading the code.
- [ ] No secret is written to disk, a cache file, a container volume, or a crash dump.
- [ ] Exception handlers do not echo request or response bodies that could contain the
      credential, and the client token is never logged (log the **accessor** instead).
- [ ] The log pipeline's masking rules were tested against a value shaped like the real
      key, not against a placeholder.
- [ ] Vault runs an **audit device**, and a read by this bot is visible in it with the path
      but without the plaintext value.

## F. Transport

- [ ] The Vault address is **HTTPS**, with certificate verification enabled and a CA the
      host actually trusts.
- [ ] The request timeout is finite and short enough that a hung Vault does not stall the
      trading loop.
- [ ] A `429` is handled by backing off, not by tight retry.

## G. Incident readiness

- [ ] Someone can revoke this bot's token by accessor, and the SecretID by its accessor,
      without redeploying anything.
- [ ] It is written down that revoking the Vault token does **not** revoke the exchange
      API key the bot already holds — the exchange-side rotation is a separate step.
