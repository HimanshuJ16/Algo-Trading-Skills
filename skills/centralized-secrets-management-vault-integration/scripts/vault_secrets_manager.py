import logging
from typing import Dict

logger = logging.getLogger(__name__)

class VaultSecretsManager:
    """
    Manages secret retrieval from HashiCorp Vault.
    Enforces AppRole authentication and environment isolation.
    Uses an in-memory cache to prevent Vault API rate-limiting.
    """
    def __init__(self, vault_addr: str, environment: str):
        self.vault_addr = vault_addr
        self.environment = environment # e.g., 'prod', 'staging', 'dev'
        self.is_authenticated = False
        self.client_token = None
        self._memory_cache: Dict[str, dict] = {}
        
        # Mock backend for testability without a live Vault server
        self._mock_vault_backend: Dict[str, dict] = {}

    def login_approle(self, role_id: str, secret_id: str):
        """Authenticates using AppRole and retrieves a client token."""
        if not role_id or not secret_id:
            raise ValueError("Must provide valid role_id and secret_id for AppRole login.")
            
        # In a real implementation, this would POST to /v1/auth/approle/login via hvac or requests
        # Mocking the successful authentication
        self.is_authenticated = True
        self.client_token = f"s.mock_token_for_{role_id}"
        logger.info(f"Successfully authenticated with Vault at {self.vault_addr} using AppRole.")

    def get_secret(self, secret_path: str) -> dict:
        """
        Retrieves a secret from the KV v2 engine.
        Enforces that the bot can only access paths within its designated environment.
        """
        if not self.is_authenticated:
            raise PermissionError("Must authenticate with Vault before reading secrets.")

        # Security Check: Enforce environment isolation
        if not secret_path.startswith(self.environment + "/"):
            logger.critical(f"Security Violation: Bot running in '{self.environment}' attempted to access '{secret_path}'")
            raise PermissionError(f"Environment mismatch. Cannot access secrets outside '{self.environment}/'")

        # Cache lookup
        if secret_path in self._memory_cache:
            logger.debug(f"Retrieved {secret_path} from in-memory cache.")
            return self._memory_cache[secret_path]

        # In a real implementation, this would GET /v1/secret/data/{secret_path}
        if secret_path not in self._mock_vault_backend:
            raise KeyError(f"Secret not found at path: {secret_path}")
            
        secret_data = self._mock_vault_backend[secret_path]
        
        # Store in cache
        self._memory_cache[secret_path] = secret_data
        logger.info(f"Successfully fetched and cached secret from {secret_path}")
        
        return secret_data

    def _inject_mock_secret(self, path: str, data: dict):
        """Helper purely for unit testing."""
        self._mock_vault_backend[path] = data
