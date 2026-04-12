"""Single interaction layer for direct Sigen API calls.

Centralizes simulation mode handling for writes and provides one-time auth
recovery when token refresh fails (refresh -> full re-auth -> retry once).
"""

from collections.abc import Awaitable, Callable
from typing import Any, Protocol
import os
import sys

from integrations.sigen_auth import get_sigen_instance, refresh_sigen_instance
from config.settings import FULL_SIMULATION_MODE, SIGEN_MODES
import logging

logger = logging.getLogger(__name__)
MODE_NAMES = {value: name for name, value in SIGEN_MODES.items()}
ACTION_DIVIDER = "*" * 96
_PURPLE = "\033[95m"
_RESET = "\033[0m"


def _divider_line() -> str:
    """Return divider line, colorized purple when terminal output supports ANSI colors."""
    force_color = os.getenv("FORCE_COLOR", "").strip().lower() in {"1", "true", "yes", "on"}
    is_tty = bool(getattr(sys.stderr, "isatty", lambda: False)())
    if (is_tty or force_color) and not os.getenv("NO_COLOR"):
        return f"{_PURPLE}{ACTION_DIVIDER}{_RESET}"
    return ACTION_DIVIDER


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
        self._simulated_operational_mode: int | None = None

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

    @staticmethod
    def _is_recoverable_auth_error(exc: Exception) -> bool:
        """Return True when exception looks like token/auth expiration failure."""
        msg = str(exc).lower()
        return any(
            token in msg
            for token in (
                "failed to refresh access token",
                "invalid grant",
                "/auth/oauth/token",
                "access token",
                "unauthorized",
            )
        )

    async def _call_with_reauth_once(
        self,
        operation: Callable[[SigenApiProtocol], Awaitable[Any]],
        operation_name: str,
    ) -> Any:
        """Run API operation and retry once after forced re-auth on auth errors."""
        try:
            return await operation(self._client)
        except Exception as exc:
            if not self._is_recoverable_auth_error(exc):
                raise

            logger.warning(
                "[AUTH RECOVERY] %s failed due to auth error: %s. "
                "Forcing full re-auth and retrying once.",
                operation_name,
                exc,
            )
            self._client = await refresh_sigen_instance()
            return await operation(self._client)

    async def get_operational_mode(self) -> Any:
        """Get the current operational mode from the inverter.
        
        Returns:
            Raw operational mode payload from the Sigen API.
        """
        if FULL_SIMULATION_MODE and self._simulated_operational_mode is not None:
            return {
                "simulated": True,
                "mode": self._simulated_operational_mode,
            }
        return await self._call_with_reauth_once(
            lambda client: client.get_operational_mode(),
            "get_operational_mode",
        )

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
            logger.info(_divider_line())
            logger.info(_divider_line())
            if FULL_SIMULATION_MODE:
                logger.info(
                    f"[SIMULATION] set_operational_mode(mode={mode_label}, value={mode}) "
                    f"- command suppressed in simulation mode"
                )
                self._simulated_operational_mode = mode
                return {"simulated": True, "mode": mode}
            else:
                logger.info(f"Setting operational mode to {mode_label} (value={mode})")
            return await self._call_with_reauth_once(
                lambda client: client.set_operational_mode(mode),
                "set_operational_mode",
            )
        finally:
            logger.info(_divider_line())
            logger.info(_divider_line())

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
        return await self._call_with_reauth_once(
            lambda client: client.get_energy_flow(),
            "get_energy_flow",
        )

    async def get_operational_modes(self) -> list[dict[str, Any]]:
        """Get the list of supported operational modes.
        
        Returns:
            List of mode dictionaries available on the inverter.
        """
        return await self._call_with_reauth_once(
            lambda client: client.get_operational_modes(),
            "get_operational_modes",
        )
