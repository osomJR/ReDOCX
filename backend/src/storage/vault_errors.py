"""Vault failures whose public messages do not expose storage or customer data."""


class VaultStorageUnavailable(RuntimeError):
    """The configured durable store cannot safely serve a request."""


class VaultIntegrityError(RuntimeError):
    """An owned item's authenticated contents or metadata failed verification."""


class VaultMigrationRequired(VaultStorageUnavailable):
    """Existing local Vault data needs an explicit, verified import."""
