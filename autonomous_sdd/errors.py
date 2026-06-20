"""Domain-specific infrastructure errors."""


class AutonomousSddError(RuntimeError):
    """Base error for runner infrastructure."""


class ConfigurationError(AutonomousSddError):
    """Raised when runtime configuration is invalid."""


class PathSafetyError(AutonomousSddError):
    """Raised when a path crosses an isolation boundary."""


class RepositoryError(AutonomousSddError):
    """Raised when a target repository cannot be inspected safely."""


class WorkspaceError(AutonomousSddError):
    """Raised when an external run workspace is invalid."""
