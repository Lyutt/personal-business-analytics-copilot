"""Deterministic errors for the Stage 3A CTV execution boundary."""


class Stage3AError(RuntimeError):
    """A fail-closed Stage 3A contract or execution error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AssetContractError(Stage3AError):
    """The checked-in authority assets do not compose as required."""


class DatasetValidationError(Stage3AError):
    """A manifest-bound dataset failed its authoritative boundary validation."""


class MetricStoreError(Stage3AError):
    """An exact Metric Store operation failed or was ambiguous."""


class ResultContractError(Stage3AError):
    """A generated result failed the frozen Result Contract."""
