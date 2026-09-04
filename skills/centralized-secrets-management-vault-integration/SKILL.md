---
name: centralized-secrets-management-vault-integration
description: >-
  Use when a trading process should fetch exchange API keys or database credentials from
  HashiCorp Vault at runtime rather than a .env file; AppRole login, KV v2 reads,
  explicit token TTL handling and a bounded local cache.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: deployment-ops
  tags: vault, hashicorp, secrets, api-keys, approle, security, kv-v2, token-lifetime
  brokers_frameworks: "HashiCorp Vault; HashiCorp Vault KV v2"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when a trading process needs **live exchange or database credentials at
runtime** and you want them to come from Vault rather than a `.env` file, a config blob,
or the process image. `VaultSecretsManager` covers the client half of the AppRole
workflow:

1. **Login** — `POST auth/approle/login` with a RoleID and SecretID, recording the
   returned `lease_duration`, `renewable`, and `accessor`.
2. **Read** — `GET {mount}/data/{path}` (KV v2), returning a `SecretBundle` that carries
   the values plus `metadata.version` so a caller can see when a secret was rotated.
3. **Hold** — a TTL-bounded cache so the process does not read Vault on every order, and
   an explicit token lifetime so the token is renewed (or the process re-authenticates)
   *before* a read fails.

The design assumption throughout is a **long-lived process**: a bot that boots once and
runs for days. That is precisely the case where a naive client breaks — its token silently
passes max TTL, or its unbounded cache keeps feeding a credential the security team
revoked hours ago.

## When NOT to Use

- **You need Vault configured, not read.** Policies, AppRole provisioning, SecretID
  delivery, and audit-device setup are operator work; see `references/workflows.md`. This
  module authenticates and reads, nothing else.
- **You expect the client to be the access-control boundary.** It is not. The
  `environment` guard rejects a malformed or wrong-environment *path string* before it
  leaves the process; it cannot constrain a token whose Vault policy is too broad. If the
  policy grants `secret/data/*`, this class will happily read whatever you ask it for
  inside its own environment prefix.
- **You are rotating a credential, not fetching one.** The hot-swap, dual-credential
  overlap, and revocation sequence belong to `secrets-rotation-without-bot-downtime`.
  This skill's contribution to rotation is bounding staleness: `cache_ttl` and
  `invalidate()`.
- **You are auditing what a key is permitted to do at the broker.** That is
  `api-key-least-privilege-audit-tool`.
- **The secret must never exist in process memory.** Vault KV hands you plaintext. For
  keys that must not leave a boundary, the operation must move to the key — see
  `hardware-security-module-hsm-for-signing-keys`.
- **You need Vault's dynamic secrets or leases.** This client reads static KV v2 and does
  not track or renew secret leases; dynamic database credentials need lease renewal and
  revocation logic this module does not implement.

## Prerequisites

- A reachable Vault server over **HTTPS**. `HttpVaultTransport` refuses `http://` unless
  `allow_insecure_http=True`, because the token and every secret would otherwise cross
  the network in clear text.
- A KV **v2** mount (`{mount}/data/{path}` reads). KV v1 has no `data`/`metadata`
  envelope and this client will not parse it.
- An AppRole whose policy is scoped to exactly the paths this process needs, and whose
  RoleID and SecretID arrive by **different channels** — HashiCorp's AppRole guidance
  treats delivering both together as an anti-pattern, and recommends response-wrapping
  the SecretID with `secret_id_num_uses=1`.
- A decision, made before deployment, about what happens when the SecretID is spent: with
  `secret_id_num_uses=1`, re-login after max TTL fails permanently and an orchestrator
  must deliver a fresh wrapped SecretID.
- Python 3.10+. No third-party package required; `hvac` can be substituted behind the
  `VaultTransport` protocol.

## Workflow

1. **Construct with the environment this process owns.**
   `VaultSecretsManager("https://vault.internal:8200", "prod", mount="secret")`. The
   environment is a single path segment and every read must begin with it.
2. **Log in at boot, once.** `login_approle(role_id, secret_id)`. Both credentials are
   retained in memory so the process can re-authenticate unattended; if that is
   unacceptable in your threat model, call `logout()` after the last read and accept that
   the process cannot recover from token expiry on its own.
3. **Classify a login failure before reacting to it.** A rejected SecretID raises
   `VaultCredentialExhausted` — Vault expires a SecretID by `secret_id_ttl` and by
   `secret_id_num_uses`, so retrying cannot succeed and the orchestrator must issue a new
   one. A 429 or 5xx raises `VaultTransportError`, which *is* worth a backed-off retry.
   Never wrap login in an unbounded retry loop: a spent SecretID would spin forever.
4. **Read secrets by path.** `get_secret("prod/binance/market-maker")` returns a
   `SecretBundle`. Hand it to the exchange client with `bundle.as_dict()` — an explicit
   call, so the plaintext never appears by accident.
5. **Distinguish the three failure modes on a read.** `VaultPathViolation` is your own
   bug (wrong environment, traversal, malformed path) and never reached the network.
   `VaultSecretNotFound` means Vault answered 404 — which means the path is absent *or*
   invisible to this policy *or* soft-deleted; check the policy before concluding the
   secret is missing. `VaultPermissionDenied` means a freshly issued token was still
   refused, i.e. the policy genuinely forbids the path.
6. **Let the manager handle the token.** Each read checks the remaining TTL and, inside
   `renew_margin`, renews via `auth/token/renew-self`. When renewal stops buying headroom
   the token has hit its max TTL, which renewal cannot extend, so the manager
   re-authenticates via AppRole exactly once. A 403 on a read likewise triggers exactly
   one re-login before the error is raised — bounded, never a loop.
7. **Bound staleness deliberately.** `cache_ttl` (default 300s) is the maximum time this
   process can keep using a credential that has since been rotated. On a rotation
   notification, call `invalidate(path)` rather than waiting out the TTL.
8. **Decide the outage policy.** With `stale_if_error=True` (default) a Vault outage lets
   the process keep trading on its last known credentials; with `False` a read raises
   instead. Choose consciously — the safe answer differs for a market maker holding
   inventory and for a batch job.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating the client's environment check as the security control.** A client-side
  prefix test is defence in depth against a bad path string. If the AppRole's Vault policy
  is broad, nothing in this module narrows it. Scope the policy; verify it with
  `sys/capabilities-self`.
- **Enforcing the prefix with `startswith("prod/")`.** `"prod/../dev/binance"` passes that
  test. The guard here splits into segments and rejects `.`, `..`, empty segments, and
  anything outside a conservative character allowlist.
- **Reading the token's expiry as "the bot is authenticated forever".** AppRole tokens
  carry a TTL and a max TTL, and renewal cannot extend past the max
  (https://developer.hashicorp.com/vault/docs/concepts/tokens). A bot that logs in at boot
  and never checks will take a 403 at an unpredictable moment — typically the first read
  after a rotation, i.e. exactly when it needs to work.
- **Caching a secret with no expiry.** The original failure mode this module was written
  against: security rotates and revokes an exchange key, the bot holds the old value in
  memory indefinitely, and the first symptom is a wall of broker `401`s mid-session.
  `cache_ttl` bounds it; `invalidate()` short-circuits it.
- **Reading interpretation into a 404.** Vault documents 404 as *"invalid path. This can
  both mean that the path truly doesn't exist or that you don't have permission to view a
  specific path"* (https://developer.hashicorp.com/vault/api-docs). Do not respond by
  creating the secret — you may be papering over a policy gap. And a KV v2 path whose
  latest version was soft-deleted also answers 404, with `data: null` and a
  `deletion_time` in the metadata.
- **Retrying a rejected login.** A spent `secret_id_num_uses` or an expired
  `secret_id_ttl` will never recover on retry. Distinguish it (`VaultCredentialExhausted`)
  from a transport failure and escalate to the orchestrator instead of looping.
- **Shipping RoleID and SecretID together.** Injecting both as environment variables from
  the same CI job collapses AppRole to a single shared password. HashiCorp's recommended
  pattern delivers the SecretID response-wrapped, single-use, and ideally CIDR-bound.
- **Logging the config object.** `print(exchange.config)` and a traceback holding the
  credential dict leak just as effectively as a hardcoded key. `SecretBundle` prints key
  *names* only; `as_dict()` is the deliberate escape hatch.
- **Re-reading Vault on every order.** Vault Community Edition supports rate-limit
  quotas, which answer `429` when exceeded
  (https://developer.hashicorp.com/vault/docs/concepts/resource-quotas). Read at boot,
  cache with a TTL, and refresh on rotation.

## Verification

- `python -m unittest discover -s skills/centralized-secrets-management-vault-integration/scripts`
  runs the suite. It drives the manager through `InMemoryVaultTransport`, a deterministic
  double that reproduces Vault's 404-for-invisible-paths behaviour, soft-deleted KV v2
  versions, token TTL/max TTL, and single-use SecretIDs.
- Regression checks worth reading before trusting a change: traversal out of the
  environment (`prod/../dev/...`), a rotated secret being picked up once `cache_ttl`
  expires, re-login on max TTL, permanent failure on a spent SecretID, and `repr` of both
  `SecretBundle` and `VaultSecretsManager` containing no secret material.
- Against a real Vault, confirm the policy — not the client — is the boundary: with the
  bot's own token, attempt a read one environment across (`vault kv get
  secret/dev/...` from a prod AppRole) and confirm Vault refuses it.
- Verify the audit device records the read, and that the recorded request contains the
  path but no plaintext value.

## Related Skills

- `secrets-rotation-without-bot-downtime`
- `api-key-least-privilege-audit-tool`
- `sandbox-credential-leakage-prevention`
- `hardware-security-module-hsm-for-signing-keys`
- `structured-logging-for-post-incident-forensics`
