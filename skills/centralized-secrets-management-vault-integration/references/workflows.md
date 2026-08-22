# Workflows for Vault Integration

Three actors appear below and their separation is the point of the design: the **Vault
operator** who writes policy, the **orchestrator** (CI, scheduler, config-management agent)
that delivers a SecretID, and the **trading process** that logs in and reads. No actor
holds both halves of the AppRole except the trading process itself.

## 1. Vault server setup (operator, once per environment)

```bash
vault secrets enable -path=secret -version=2 kv
vault auth enable approle
vault audit enable file file_path=/var/log/vault/audit.log
```

Write a policy scoped to one workload. Note that a wildcard path does **not** grant the
parent path, so grant both if the workload reads the parent:

```hcl
# prod-binance-mm.hcl
path "secret/data/prod/binance/market-maker" {
  capabilities = ["read"]
}
path "secret/data/prod/binance/market-maker/*" {
  capabilities = ["read"]
}
# Deny wins over any other grant, including sudo. Belt and braces against a
# future broad grant added elsewhere in the policy set.
path "secret/data/prod/kraken/*" {
  capabilities = ["deny"]
}
```

```bash
vault policy write prod-binance-mm prod-binance-mm.hcl
```

## 2. AppRole provisioning (operator)

```bash
vault write auth/approle/role/prod-binance-mm \
    token_policies="prod-binance-mm" \
    token_ttl=20m \
    token_max_ttl=2h \
    secret_id_ttl=10m \
    secret_id_num_uses=1 \
    secret_id_bound_cidrs="10.20.30.0/24" \
    token_bound_cidrs="10.20.30.0/24"
```

Choose `token_ttl` / `token_max_ttl` and `secret_id_num_uses` together, because they
decide what happens at hour two:

| Configuration | Behaviour after `token_max_ttl` | Use when |
|---|---|---|
| `secret_id_num_uses=1` | Re-login fails permanently (`VaultCredentialExhausted`). The orchestrator must deliver a new wrapped SecretID. | An orchestrator is present and can re-deliver on demand. Strongest posture. |
| `secret_id_num_uses=0` (unlimited), short `secret_id_ttl` | Re-login succeeds until the SecretID's own TTL expires. | A restart-tolerant process with a supervisor that re-injects credentials. |
| Periodic token (`period=…` on the role) | The token never hits a max TTL as long as renewal keeps succeeding. | A long-lived process that must survive unattended for days. Discuss with the Vault operators before assuming it. |

## 3. SecretID delivery (orchestrator)

The RoleID is not a secret and may be baked into the image or instance metadata. The
SecretID is a password equivalent and travels separately, response-wrapped:

```bash
# Orchestrator, holding only a narrow token permitted to generate SecretIDs:
vault write -wrap-ttl=120s -f auth/approle/role/prod-binance-mm/secret-id
# -> returns wrapping_token, not the SecretID itself
```

The trading host unwraps once:

```bash
VAULT_TOKEN="$WRAPPING_TOKEN" vault unwrap
```

An unwrap that fails because the token was already used is **evidence of interception**,
not a transient error: the wrapping token is single-use. Treat it as an incident, revoke
the SecretID, and do not simply request another.

Anti-pattern to reject in review: a single CI job that exports both `VAULT_ROLE_ID` and
`VAULT_SECRET_ID` as plaintext environment variables. That collapses AppRole into one
shared password and puts it in the CI logs.

## 4. Bot runtime (trading process)

```python
from vault_secrets_manager import (
    VaultSecretsManager,
    VaultCredentialExhausted,
    VaultSecretNotFound,
    VaultTransportError,
)

manager = VaultSecretsManager(
    vault_addr="https://vault.internal:8200",
    environment="prod",          # single leading path segment this bot may read
    mount="secret",              # KV v2 mount
    cache_ttl=300.0,             # bounds staleness after a rotation
    renew_margin=60.0,           # renew before the token gets this close to expiry
    stale_if_error=True,         # a Vault outage must not stop a live bot
)

try:
    manager.login_approle(role_id, secret_id)
except VaultCredentialExhausted:
    # Permanent: the SecretID is spent or expired. Escalate to the orchestrator
    # for a fresh wrapped SecretID. Retrying here would loop forever.
    raise
except VaultTransportError:
    # Transient: back off and retry a bounded number of times.
    raise

bundle = manager.get_secret("prod/binance/market-maker")
exchange = ExchangeClient(**bundle.as_dict())   # explicit; nothing leaks by accident
logger.info("Loaded credentials %s", bundle)    # prints key names only
```

Reads after boot come from the cache until `cache_ttl` elapses. Token renewal and, at max
TTL, re-authentication happen inside `get_secret`; the caller does not schedule them.

## 5. Rotation (operator + trading process)

1. Operator writes a new KV v2 version at the same path. `metadata.version` increments.
2. The bot picks it up either when `cache_ttl` expires, or immediately if it is told to:

```python
manager.invalidate("prod/binance/market-maker")
new_bundle = manager.get_secret("prod/binance/market-maker")
if new_bundle.version != bundle.version:
    exchange.swap_credentials(new_bundle.as_dict())
```

3. Only after the bot confirms the new credential works does the operator revoke the old
   one at the exchange. The hot-swap and rollback sequence is
   `secrets-rotation-without-bot-downtime`; this skill's job is to make the new value
   reachable and to bound how long the old one can linger.

## 6. Incident: suspected host compromise

```bash
vault list auth/token/accessors                        # find the bot's token
vault token revoke -accessor <accessor>                # kill the token
vault write -f auth/approle/role/prod-binance-mm/secret-id-accessor/destroy \
    secret_id_accessor=<secret_id_accessor>            # kill the SecretID
```

Revoking the Vault token does **not** revoke the exchange API key the bot already holds in
memory or already used. Rotate at the exchange as well, and treat the memory-resident copy
as compromised from the moment the host was. See
`post-incident-forensics-for-suspected-key-compromise`.
