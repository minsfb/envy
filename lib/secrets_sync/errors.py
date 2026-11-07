"""Only constant, value-free messages cross this module's error boundary."""


class SyncError(Exception):
    code = 1


class UsageError(SyncError):
    code = 2


class ProviderUnavailable(SyncError):
    """A declared provider is missing a runtime prerequisite, not a credential."""


class CommittedError(SyncError):
    code = 3


class Cancelled(SyncError):
    code = 130
