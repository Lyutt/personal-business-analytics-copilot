"""Explicit Provider and Adapter contracts; no automatic fallback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .errors import PageContractDrift, ProviderCapabilityError, SessionRecoveryFailed


class SessionExpired(RuntimeError):
    """Raised by a page driver when the authenticated page session expired."""


class PageDriver(Protocol):
    """Narrow injected Playwright-facing page interface."""

    def enter_exact_module(self, module_id: str) -> None: ...
    def select_exact_template(self, template_id: str) -> None: ...
    def set_exact_parameters(self, parameters: Mapping[str, Any]) -> None: ...
    def read_parameter_values(self) -> Mapping[str, Any]: ...
    def execute_query(self) -> None: ...
    def read_result(self) -> tuple[Sequence[str], Sequence[Mapping[str, Any]]]: ...
    def refresh(self) -> None: ...


@dataclass(frozen=True)
class QueryContract:
    adapter_id: str
    adapter_version: str
    provider_id: str
    source_id: str
    dataset_id: str
    query_asset_id: str
    module_id: str
    template_id: str
    parameters: Mapping[str, Any]
    expected_columns: tuple[str, ...]
    page_contract_version: str


@dataclass(frozen=True)
class AdapterResult:
    normalized_parameter_readback: Mapping[str, Any]
    columns: tuple[str, ...]
    rows: tuple[Mapping[str, Any], ...]
    session_status_code: str
    recovery_refresh_count: int
    recovery_query_count: int


class ExactWebQueryAdapter:
    """Apollo/NovaBI adapter enforcing exact templates, parameters, and schema."""

    def __init__(self, driver: PageDriver, contract: QueryContract) -> None:
        self.driver = driver
        self.contract = contract

    def acquire(self) -> AdapterResult:
        try:
            return self._query_once("active", 0, 0)
        except SessionExpired:
            self.driver.refresh()
            try:
                return self._query_once("recovered_after_single_refresh", 1, 1)
            except (SessionExpired, PageContractDrift) as exc:
                raise SessionRecoveryFailed("Single allowed recovery query failed") from exc

    def _query_once(self, session_status: str, refresh_count: int, recovery_queries: int) -> AdapterResult:
        try:
            self.driver.enter_exact_module(self.contract.module_id)
            self.driver.select_exact_template(self.contract.template_id)
        except LookupError as exc:
            raise PageContractDrift("ADAPTER_PAGE_CONTRACT_DRIFT") from exc
        self.driver.set_exact_parameters(self.contract.parameters)
        readback = dict(self.driver.read_parameter_values())
        if readback != dict(self.contract.parameters):
            raise PageContractDrift("Exact parameter readback does not match the Query Contract")
        self.driver.execute_query()
        columns, rows = self.driver.read_result()
        normalized_columns = tuple(columns)
        if normalized_columns != self.contract.expected_columns:
            raise PageContractDrift("Exact result schema does not match the Query Contract")
        return AdapterResult(
            normalized_parameter_readback=readback,
            columns=normalized_columns,
            rows=tuple(dict(row) for row in rows),
            session_status_code=session_status,
            recovery_refresh_count=refresh_count,
            recovery_query_count=recovery_queries,
        )


class ApolloQueryAdapter(ExactWebQueryAdapter):
    """Versioned internal Apollo Adapter; unrelated to Apollo.io."""

    EXPECTED_ADAPTER_ID = "ADP_INTERNAL_APOLLO_QUERY_V1"

    def __init__(self, driver: PageDriver, contract: QueryContract) -> None:
        if contract.adapter_id != self.EXPECTED_ADAPTER_ID:
            raise ValueError("Apollo Adapter requires the exact registered Adapter ID")
        super().__init__(driver, contract)


class NovaBIQueryAdapter(ExactWebQueryAdapter):
    """Versioned NovaBI Adapter."""

    EXPECTED_ADAPTER_ID = "ADP_NOVABI_QUERY_V1"

    def __init__(self, driver: PageDriver, contract: QueryContract) -> None:
        if contract.adapter_id != self.EXPECTED_ADAPTER_ID:
            raise ValueError("NovaBI Adapter requires the exact registered Adapter ID")
        super().__init__(driver, contract)


class OutlookProvider(Protocol):
    provider_id: str

    def capabilities(self) -> set[str]: ...


OUTLOOK_REQUIRED_CAPABILITIES = {
    "contract_scoped_mailbox_search",
    "matched_message_metadata_read_without_state_change",
    "matched_manifest_attachment_download",
    "draft_create_without_send",
}


def validate_outlook_provider(provider: OutlookProvider) -> None:
    missing = OUTLOOK_REQUIRED_CAPABILITIES - provider.capabilities()
    if missing:
        raise ProviderCapabilityError(
            f"Outlook Provider capability validation blocked; missing {sorted(missing)}"
        )


class AdapterRegistry:
    """Exact-ID registry; source-name or similarity matching is unavailable."""

    def __init__(self) -> None:
        self._adapters: dict[str, object] = {}

    def register(self, adapter_id: str, adapter: object) -> None:
        if adapter_id in self._adapters:
            raise ValueError(f"Adapter already registered: {adapter_id}")
        self._adapters[adapter_id] = adapter

    def require(self, adapter_id: str) -> object:
        try:
            return self._adapters[adapter_id]
        except KeyError as exc:
            raise ProviderCapabilityError(
                f"Explicit Adapter is not configured: {adapter_id}; fallback is prohibited"
            ) from exc
