"""Single interaction layer for direct Sigen API calls.

Centralizes simulation mode handling for writes and provides one-time auth
recovery when token refresh fails (refresh -> full re-auth -> retry once).
"""

from collections.abc import Awaitable, Callable
from typing import Any, Protocol
import logging

from integrations.sigen_auth import get_sigen_instance, refresh_sigen_instance
from config.settings import FULL_SIMULATION_MODE, SIGEN_MODE_NAMES, SIGEN_MODES
from utils.terminal_formatting import ANSI_PURPLE, colorize_text

logger = logging.getLogger(__name__)
MODE_NAMES = SIGEN_MODE_NAMES
ACTION_DIVIDER = "─" * 96


class SigenPayloadError(Exception):
    """Raised when the Sigen API returns a structurally unexpected payload."""


def _divider_line() -> str:
    """Return divider line, colorized purple when terminal output supports ANSI colors."""
    return colorize_text(ACTION_DIVIDER, ANSI_PURPLE)


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

    @staticmethod
    def _is_missing_data_key_error(exc: Exception) -> bool:
        """Return True when upstream response parsing failed on missing 'data' key.

        The Sigen API wraps all responses in a top-level ``data`` envelope.  A
        ``KeyError`` whose first argument is exactly ``"data"`` means that
        envelope was absent — a known intermittent upstream quirk.
        """
        if not isinstance(exc, KeyError):
            return False
        return exc.args[0] == "data" if exc.args else False

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
        operation = lambda client: client.get_energy_flow()
        try:
            return await self._call_with_reauth_once(operation, "get_energy_flow")
        except KeyError as exc:
            # Some upstream payload variants intermittently omit expected wrappers
            # (for example, missing 'data'). Retry once before surfacing failure.
            if not self._is_missing_data_key_error(exc):
                raise
            diagnostic_attrs: dict[str, str] = {}
            for attr_name in (
                "response",
                "raw_response",
                "payload",
                "data",
                "body",
                "status_code",
                "request",
            ):
                if hasattr(exc, attr_name):
                    try:
                        diagnostic_attrs[attr_name] = repr(getattr(exc, attr_name))
                    except Exception:  # pragma: no cover - defensive logging only
                        diagnostic_attrs[attr_name] = "<unavailable>"

            logger.error(
                "[ENERGY FLOW] First fetch failed before payload could be returned. "
                "exc_type=%s exc_args=%s exc_details=%s",
                type(exc).__name__,
                exc.args,
                diagnostic_attrs,
                exc_info=True,
            )
            logger.warning(
                "[ENERGY FLOW] Missing expected key during fetch (%s). "
                "Retrying once.",
                exc,
            )
            try:
                return await self._call_with_reauth_once(operation, "get_energy_flow(retry)")
            except KeyError as retry_exc:
                if not self._is_missing_data_key_error(retry_exc):
                    raise
                logger.error(
                    "[ENERGY FLOW] Retry also failed due to missing expected key (%s). "
                    "Raising SigenPayloadError.",
                    retry_exc,
                    exc_info=True,
                )
                raise SigenPayloadError(
                    f"get_energy_flow: missing expected 'data' key after retry "
                    f"(exc_args={retry_exc.args!r})"
                ) from retry_exc

    async def get_operational_modes(self) -> list[dict[str, Any]]:
        """Get the list of supported operational modes.
        
        Returns:
            List of mode dictionaries available on the inverter.
        """
        return await self._call_with_reauth_once(
            lambda client: client.get_operational_modes(),
            "get_operational_modes",
        )
