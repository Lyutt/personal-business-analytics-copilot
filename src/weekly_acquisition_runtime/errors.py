"""Runtime errors mapped to frozen acquisition contract outcomes."""


class AcquisitionError(RuntimeError):
    """Base acquisition contract violation."""


class ContractViolation(AcquisitionError):
    """A frozen contract invariant was violated."""


class AmbiguousBindingError(ContractViolation):
    """More than one explicit candidate exists for one business key."""


class UnboundInputError(ContractViolation):
    """A Pipeline attempted to consume an input without an explicit binding."""


class StorageBoundaryError(ContractViolation):
    """Runtime storage is inside Git, OneDrive, or outside its configured root."""


class ImmutableArtifactError(ContractViolation):
    """An immutable Attempt artifact would be overwritten."""


class BrowserLockOccupied(AcquisitionError):
    """The global Browser Acquisition Lock is already held; no queue is used."""


class ProviderCapabilityError(AcquisitionError):
    """The selected Provider lacks a required capability; fallback is prohibited."""


class SessionRecoveryFailed(AcquisitionError):
    """The single allowed session recovery sequence did not succeed."""


class PageContractDrift(AcquisitionError):
    """The exact registered page contract is no longer present."""
