"""
sigen_interaction.py
--------------------
Single interaction layer for all direct Sigen API calls.
Centralizes simulation mode handling for all write operations.
"""

from typing import Any, Protocol

from integrations.sigen_auth import get_sigen_instance
from config.settings import FULL_SIMULATION_MODE, SIGEN_MODES
import logging

logger = logging.getLogger(__name__)
MODE_NAMES = {value: name for name, value in SIGEN_MODES.items()}


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
        """Create a SigenInteraction instance by authenticating with the Sigen API.
        
        Returns:
            A new SigenInteraction instance with an authenticated client.
        """
        client = await get_sigen_instance()
        return cls(client)

    @classmethod
    def from_client(cls, client: SigenApiProtocol) -> "SigenInteraction":
        """Factory for tests and dependency injection scenarios."""
        return cls(client)

    async def get_operational_mode(self) -> Any:
        """Get the current operational mode from the inverter.
        
        Returns:
            Raw operational mode payload from the Sigen API.
        """
        return await self._client.get_operational_mode()

    async def set_operational_mode(self, mode: int) -> Any:
        """Set the operational mode.
        
        In FULL_SIMULATION_MODE, logs the action but does not send the command
        to the inverter. In live mode, sends the mode to the real Sigen API.
        
        Args:
            mode: Operational mode integer from SIGEN_MODES.
            
        Returns:
            Response dict from the API or simulator.
        """
        mode_label = MODE_NAMES.get(mode, f"UNKNOWN({mode})")
        try:
            logger.info("************************************************************************************************")
            logger.info("************************************************************************************************")
            if FULL_SIMULATION_MODE:
                logger.info(
                    f"[SIMULATION] set_operational_mode(mode={mode_label}, value={mode}) "
                    f"- command suppressed in simulation mode"
                )
                return {"simulated": True, "mode": mode}
            else:
                logger.info(f"Setting operational mode to {mode_label} (value={mode})")
            return await self._client.set_operational_mode(mode)
        finally:
            logger.info("************************************************************************************************")
            logger.info("************************************************************************************************")

    async def export_to_grid(self, num_mins: int) -> Any:
        """Switch the inverter to fully fed-to-grid mode.

        This method only performs the mode switch. The scheduler controls how long
        export stays active and when to restore the previous mode.

        Args:
            num_mins: Intended active export duration in minutes, used for logging.

        Returns:
            Response dict from the API or simulator.
        """
        duration_minutes = max(1, int(num_mins))
        logger.info(
            "[TIMED EXPORT] Requesting GRID_EXPORT for %s minutes (scheduler-managed restore).",
            duration_minutes,
        )
        response = await self.set_operational_mode(SIGEN_MODES["GRID_EXPORT"])
        if isinstance(response, dict):
            response.setdefault("timed_export_minutes", duration_minutes)
        return response

    async def get_energy_flow(self) -> dict[str, Any]:
        """Get current energy flow telemetry from the inverter.
        
        Returns:
            Raw energy_flow payload with PV power, battery state, exports, etc.
        """
        return await self._client.get_energy_flow()

    async def get_operational_modes(self) -> list[dict[str, Any]]:
        """Get the list of supported operational modes.
        
        Returns:
            List of mode dictionaries available on the inverter.
        """
        return await self._client.get_operational_modes()
