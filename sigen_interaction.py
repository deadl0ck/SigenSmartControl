"""
sigen_interaction.py
--------------------
Single interaction layer for all direct Sigen API calls.
"""

from typing import Any, Protocol

from sigen_auth import get_sigen_instance


class SigenApiProtocol(Protocol):
    """Protocol for the subset of Sigen client API used by this project."""

    async def get_operational_mode(self) -> Any:
        ...

    async def set_operational_mode(self, mode: int, profile_id: int = -1) -> Any:
        ...

    async def get_energy_flow(self) -> dict[str, Any]:
        ...

    async def get_operational_modes(self) -> list[dict[str, Any]]:
        ...


class SigenInteraction:
    """Thin wrapper around the authenticated Sigen client."""

    def __init__(self, client: SigenApiProtocol) -> None:
        self._client = client

    @classmethod
    async def create(cls) -> "SigenInteraction":
        client = await get_sigen_instance()
        return cls(client)

    @classmethod
    def from_client(cls, client: SigenApiProtocol) -> "SigenInteraction":
        """Factory for tests and dependency injection scenarios."""
        return cls(client)

    async def get_operational_mode(self) -> Any:
        return await self._client.get_operational_mode()

    async def set_operational_mode(self, mode: int, profile_id: int = -1) -> Any:
        return await self._client.set_operational_mode(mode, profile_id)

    async def get_energy_flow(self) -> dict[str, Any]:
        return await self._client.get_energy_flow()

    async def get_operational_modes(self) -> list[dict[str, Any]]:
        return await self._client.get_operational_modes()
