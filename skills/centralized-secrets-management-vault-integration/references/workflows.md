# Workflows for Vault Integration

1. **Vault Server Setup (Infra Team)**:
   - Enable KV V2 secrets engine.
   - Enable AppRole authentication.
   - Write a policy restricting read access:
     ```hcl
     path "secret/data/prod/exchange_x/*" {
       capabilities = ["read"]
     }
     ```
2. **AppRole Provisioning**:
   - Generate a `role_id` and a wrapped `secret_id`. 
   - The deployment pipeline (e.g., Jenkins/GitHub Actions) securely injects these two variables into the runtime environment of the trading bot.
3. **Bot Runtime**:
   - The Python bot imports `VaultSecretsManager`.
   - The manager authenticates via the `/v1/auth/approle/login` endpoint.
   - The bot fetches its configuration: `manager.get_secret('prod/exchange_x/api_keys')`.
   - The bot establishes its WebSocket/FIX session using the retrieved keys.
