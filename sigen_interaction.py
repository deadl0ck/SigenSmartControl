"""
sigen_interaction.py
--------------------
Single interaction layer for all direct Sigen API calls.
Centralizes simulation mode handling for all write operations.
"""

from typing import Any, Protocol

from sigen_auth import get_sigen_instance
from config import FULL_SIMULATION_MODE
import logging

logger = logging.getLogger(__name__)


class SigenApiProtocol(Protocol):
    """Protocol for the subset of Sigen client API used by this project."""

    async def get_operational_mode(self) -> Any:
        ...

    async def set_operational_mode(self, mode: int) -> Any:
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

    async def set_operational_mode(self, mode: int) -> Any:
        """
        Set the operational mode. In FULL_SIMULATION_MODE, logs the action
        but does not send the command to the inverter.
        """
        try:
            logger.info("************************************************************************************************")
            logger.info("************************************************************************************************")
            if FULL_SIMULATION_MODE:
                logger.info(
                    f"[SIMULATION] set_operational_mode(mode={mode}) "
                    f"- command suppressed in simulation mode"
                )
                return {"simulated": True, "mode": mode}
            else:
                logger.info(f"Setting operational mode to {mode}")
            return await self._client.set_operational_mode(mode)
        finally:
            logger.info("************************************************************************************************")
            logger.info("************************************************************************************************")

    async def get_energy_flow(self) -> dict[str, Any]:
        return await self._client.get_energy_flow()

    async def get_operational_modes(self) -> list[dict[str, Any]]:
        return await self._client.get_operational_modes()
