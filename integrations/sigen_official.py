"""Official Sigen OpenAPI client.

Provides a protocol-compatible client implementation that can be swapped into the
existing interaction layer. This implementation supports account-based and key-based
auth flows and uses configurable endpoint paths so it can be adapted to portal/API
path changes without code edits.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import aiohttp
from dotenv import load_dotenv

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
except ImportError:  # pragma: no cover - optional dependency fallback
    AES = None
    pad = None

logger = logging.getLogger(__name__)


REGION_OPENAPI_BASE_URLS = {
    "eu": "https://openapi-eu.sigencloud.com",
    "cn": "https://openapi-cn.sigencloud.com",
    "apac": "https://openapi-apac.sigencloud.com",
    "us": "https://openapi-us.sigencloud.com",
}


OFFICIAL_MODE_INT_TO_ENUM: dict[int, str] = {
    0: "MSC",  # Maximum Self-Consumption
    5: "FFG",  # Fully Feed-in to Grid
    8: "NBI",  # North Bound
}
OFFICIAL_MODE_ENUM_TO_INT: dict[str, int] = {
    value: key for key, value in OFFICIAL_MODE_INT_TO_ENUM.items()
}

# Observed through account-password official endpoints. These values align with
# the legacy cloud client but are not all listed in the public enum page.
OBSERVED_ACCOUNT_MODE_INT_TO_LABEL: dict[int, str] = {
    0: "Maximum Self-Consumption",
    1: "Sigen AI Mode",
    2: "TOU",
    5: "Fully Feed-in to Grid",
    7: "Remote EMS Mode",
    8: "North Bound",
    9: "Custom Operation Mode",
}


@dataclass(frozen=True)
class OfficialPaths:
    """HTTP endpoint paths for the official OpenAPI client."""

    auth_account: str = "/openapi/auth/login/password"
    auth_key: str = "/auth/oauth/token"
    system_list: str = "/openapi/system"
    system_summary: str = "/openapi/systems/{systemId}/summary"
    query_mode: str = "/openapi/instruction/{systemId}/settings"
    switch_mode: str = "/openapi/instruction/settings"
    energy_flow: str | None = "/openapi/systems/{systemId}/energyFlow"
    device_realtime: str | None = "/openapi/systems/{systemId}/device/realtime"


class SigenOfficial:
    """Official OpenAPI-backed client implementing project-required methods."""

    def __init__(
        self,
        *,
        username: str | None = None,
        password: str | None = None,
        app_key: str | None = None,
        app_secret: str | None = None,
        system_id: str | None = None,
        region: str = "eu",
        base_url: str | None = None,
        auth_mode: str = "account",
        strict_official_only: bool = False,
        timeout_seconds: int = 20,
        paths: OfficialPaths | None = None,
    ) -> None:
        """Initialize official client configuration.

        Args:
            username: Sigen account username for account auth mode.
            password: Sigen account password for account auth mode.
            app_key: App key for key auth mode.
            app_secret: App secret for key auth mode.
            system_id: Optional preconfigured system ID.
            region: One of eu/cn/apac/us.
            base_url: Optional override base URL.
            auth_mode: account or key.
            strict_official_only: If True, disables legacy-compatible auth fallback.
            timeout_seconds: Request timeout in seconds.
            paths: Endpoint path overrides.

        Raises:
            ValueError: If region is unsupported.
        """
        if region not in REGION_OPENAPI_BASE_URLS:
            supported = ", ".join(REGION_OPENAPI_BASE_URLS)
            raise ValueError(f"Unsupported region '{region}'. Supported regions are: {supported}")

        self.username = username
        self.password = password
        self.app_key = app_key
        self.app_secret = app_secret
        self.system_id = system_id
        self.auth_mode = auth_mode.lower().strip()
        self.strict_official_only = strict_official_only
        self.base_url = (base_url or REGION_OPENAPI_BASE_URLS[region]).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.paths = paths or OfficialPaths()

        self.access_token: str | None = None
        self.operational_modes: list[dict[str, str]] = [
            {"value": str(value), "label": label}
            for value, label in OBSERVED_ACCOUNT_MODE_INT_TO_LABEL.items()
        ]

    @classmethod
    async def create_from_env(cls) -> "SigenOfficial":
        """Create and initialize an official client from environment variables.

        This helper loads `.env`, resolves auth mode and endpoint overrides, and
        performs full async initialization (auth + system resolution).

        Returns:
            Initialized `SigenOfficial` client instance ready for API calls.

        Raises:
            RuntimeError: If authentication fails or no system can be resolved.
            ValueError: If configured region is unsupported.
        """
        load_dotenv()

        region = os.getenv("SIGEN_REGION", "eu")
        auth_mode = os.getenv("SIGEN_OFFICIAL_AUTH_MODE", "account").strip().lower()

        client = cls(
            username=os.getenv("SIGEN_USERNAME"),
            password=os.getenv("SIGEN_PASSWORD"),
            app_key=os.getenv("SIGEN_APP_KEY"),
            app_secret=os.getenv("SIGEN_APP_SECRET"),
            system_id=os.getenv("SIGEN_SYSTEM_ID"),
            region=region,
            base_url=os.getenv("SIGEN_OFFICIAL_BASE_URL"),
            auth_mode=auth_mode,
            strict_official_only=os.getenv("SIGEN_OFFICIAL_STRICT_ONLY", "false").strip().lower()
            in {"1", "true", "yes", "on"},
            paths=OfficialPaths(
                auth_account=os.getenv(
                    "SIGEN_OFFICIAL_AUTH_ACCOUNT_PATH",
                    "/openapi/auth/login/password",
                ),
                auth_key=os.getenv("SIGEN_OFFICIAL_AUTH_KEY_PATH", "/auth/oauth/token"),
                system_list=os.getenv("SIGEN_OFFICIAL_SYSTEM_LIST_PATH", "/openapi/system"),
                system_summary=os.getenv(
                    "SIGEN_OFFICIAL_SYSTEM_SUMMARY_PATH",
                    "/openapi/systems/{systemId}/summary",
                ),
                query_mode=os.getenv(
                    "SIGEN_OFFICIAL_QUERY_MODE_PATH",
                    "/openapi/instruction/{systemId}/settings",
                ),
                switch_mode=os.getenv(
                    "SIGEN_OFFICIAL_SWITCH_MODE_PATH",
                    "/openapi/instruction/settings",
                ),
                energy_flow=os.getenv(
                    "SIGEN_OFFICIAL_ENERGY_FLOW_PATH",
                    "/openapi/systems/{systemId}/energyFlow",
                ),
                device_realtime=os.getenv(
                    "SIGEN_OFFICIAL_DEVICE_REALTIME_PATH",
                    "/openapi/systems/{systemId}/device/realtime",
                ),
            ),
        )
        await client.async_initialize()
        return client

    async def async_initialize(self) -> None:
        """Authenticate and resolve a default system ID when not preconfigured.

        Returns:
            None.

        Raises:
            RuntimeError: If authentication fails, no systems are returned, or the
                system list response does not include a usable `systemId`.
        """
        await self.authenticate()

        if self.system_id:
            return

        systems = await self.get_system_list()
        if not systems:
            raise RuntimeError("No systems returned by official API. Set SIGEN_SYSTEM_ID in .env.")

        first_system_id = str(systems[0].get("systemId", "")).strip()
        if not first_system_id:
            raise RuntimeError("Official API response missing systemId in system list.")
        self.system_id = first_system_id

    async def authenticate(self) -> None:
        """Authenticate against the official API using configured auth mode.

        Returns:
            None.

        Raises:
            RuntimeError: If authentication does not yield an access token.
        """
        if self.auth_mode == "key":
            await self._authenticate_with_key()
        else:
            await self._authenticate_with_account()

        if not self.access_token:
            raise RuntimeError("Authentication succeeded but no access token was returned.")

    async def _authenticate_with_account(self) -> None:
        """Authenticate using account credentials with compatibility fallbacks.

        Tries multiple request formats because tenant deployments can differ in
        accepted payload shape and encoding.

        Returns:
            None.

        Raises:
            RuntimeError: If all account-auth variants fail.
        """
        if not self.username or not self.password:
            raise RuntimeError(
                "SIGEN_USERNAME and SIGEN_PASSWORD are required for account auth mode."
            )

        # Try multiple auth payload styles because tenants/environments may differ.
        # 1) form-encoded plain password (official docs indicate x-www-form-urlencoded)
        # 2) form-encoded encrypted password (legacy cloud compatibility)
        # 3) JSON plain password (fallback for variants that expect JSON)
        attempts: list[tuple[str, dict[str, str], bool]] = [
            (
                "form-plain",
                {
                    "username": self.username,
                    "password": self.password,
                },
                True,
            ),
        ]

        encrypted_password = self._encrypt_password_if_available(self.password)
        if encrypted_password != self.password:
            attempts.append(
                (
                    "form-encrypted",
                    {
                        "username": self.username,
                        "password": encrypted_password,
                    },
                    True,
                )
            )

        attempts.append(
            (
                "json-plain",
                {
                    "username": self.username,
                    "password": self.password,
                },
                False,
            )
        )

        last_error: Exception | None = None
        for attempt_name, payload, use_form in attempts:
            try:
                response = await self._request(
                    method="POST",
                    path=self.paths.auth_account,
                    payload=payload,
                    include_bearer=False,
                    use_form_urlencoded=use_form,
                )
                data = self._normalize_data(response)
                self.access_token = data.get("accessToken") or data.get("access_token")
                if self.access_token:
                    logger.info("Official account auth succeeded using %s payload.", attempt_name)
                    return
            except Exception as exc:
                last_error = exc
                logger.warning("Official account auth attempt '%s' failed: %s", attempt_name, exc)

        if self.strict_official_only:
            raise RuntimeError(
                "Official-only mode is enabled and all official account auth attempts failed. "
                "Disable SIGEN_OFFICIAL_STRICT_ONLY to allow compatibility fallbacks."
            ) from last_error

        # Fallback: some tenants accept legacy-style account token requests.
        try:
            encrypted_password = self._encrypt_password_if_available(self.password)
            legacy_payload = {
                "username": self.username,
                "password": encrypted_password,
                "grant_type": "password",
            }
            auth_base_url = os.getenv("SIGEN_OFFICIAL_AUTH_BASE_URL")
            response = await self._request(
                method="POST",
                path=self.paths.auth_account,
                payload=legacy_payload,
                include_bearer=False,
                use_form_urlencoded=True,
                base_url_override=auth_base_url,
                basic_auth_username="sigen",
                basic_auth_password="sigen",
            )
            data = self._normalize_data(response)
            self.access_token = (
                data.get("accessToken")
                or data.get("access_token")
                or data.get("token")
            )
            if self.access_token:
                logger.info(
                    "Official account auth succeeded using legacy-compatible token flow."
                )
                return
        except Exception as exc:
            last_error = exc
            logger.warning("Legacy-compatible account auth fallback failed: %s", exc)

        raise RuntimeError(
            "Official account authentication failed for all payload formats. "
            "Verify username/password, auth path, and account permissions."
        ) from last_error

    async def _authenticate_with_key(self) -> None:
        """Authenticate using app key/secret credentials.

        Returns:
            None.

        Raises:
            RuntimeError: If key credentials are missing or token acquisition fails.
        """
        if not self.app_key or not self.app_secret:
            raise RuntimeError("SIGEN_APP_KEY and SIGEN_APP_SECRET are required for key auth mode.")

        encoded_key = base64.b64encode(f"{self.app_key}:{self.app_secret}".encode("utf-8")).decode(
            "utf-8"
        )
        payload = {
            "key": encoded_key,
        }
        response = await self._request(
            method="POST",
            path=self.paths.auth_key,
            payload=payload,
            include_bearer=False,
            use_form_urlencoded=False,
        )
        data = self._normalize_data(response)
        self.access_token = data.get("accessToken") or data.get("access_token")

    @staticmethod
    def _encrypt_password_if_available(password: str) -> str:
        """Encrypt password with legacy AES-CBC method when Crypto is available.

        Some account-auth variants still expect encrypted password values.

        Args:
            password: Plain-text password.

        Returns:
            Encrypted base64 password if encryption dependencies are available,
            otherwise returns plain password unchanged.
        """
        if AES is None or pad is None:
            return password

        key = "sigensigensigenp"
        iv = "sigensigensigenp"
        cipher = AES.new(key.encode("utf-8"), AES.MODE_CBC, iv.encode("latin1"))
        encrypted = cipher.encrypt(pad(password.encode("utf-8"), AES.block_size))
        return base64.b64encode(encrypted).decode("utf-8")

    async def _request(
        self,
        *,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        include_bearer: bool = True,
        use_form_urlencoded: bool = False,
        base_url_override: str | None = None,
        basic_auth_username: str | None = None,
        basic_auth_password: str | None = None,
        query_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send an HTTP request to the official API and return response JSON.

        Args:
            method: HTTP method.
            path: Endpoint path.
            payload: JSON request body payload.
            include_bearer: Whether to include Authorization bearer header.
            use_form_urlencoded: If True, send payload as form data.
            base_url_override: Optional base URL override for this request.
            basic_auth_username: Optional Basic Auth username.
            basic_auth_password: Optional Basic Auth password.
            query_params: Optional query parameters.

        Returns:
            Decoded JSON response.

        Raises:
            RuntimeError: If HTTP status is non-success or API code is non-zero.
        """
        normalized_path = path if path.startswith("/") else f"/{path}"
        base_url = (base_url_override or self.base_url).rstrip("/")
        url = f"{base_url}{normalized_path}"

        headers = {
            "Content-Type": (
                "application/x-www-form-urlencoded" if use_form_urlencoded else "application/json"
            )
        }
        if include_bearer and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            request_kwargs: dict[str, Any]
            if use_form_urlencoded:
                request_kwargs = {"data": payload}
            elif method.upper() == "GET":
                request_kwargs = {}
            else:
                request_kwargs = {"json": payload}

            if query_params:
                request_kwargs["params"] = query_params

            if basic_auth_username is not None and basic_auth_password is not None:
                request_kwargs["auth"] = aiohttp.BasicAuth(
                    basic_auth_username,
                    basic_auth_password,
                )

            async with session.request(method, url, headers=headers, **request_kwargs) as response:
                body: Any
                try:
                    body = await response.json(content_type=None)
                except Exception:
                    body = await response.text()

                if response.status >= 400:
                    raise RuntimeError(f"{method} {url} failed ({response.status}): {body}")

        code = body.get("code") if isinstance(body, dict) else None
        if code not in (None, 0, "0"):
            raise RuntimeError(f"Official API returned non-success code {code}: {body}")
        if not isinstance(body, dict):
            raise RuntimeError(f"Expected JSON object response from {method} {url}, got: {body}")
        return body

    @staticmethod
    def _normalize_data(response: dict[str, Any]) -> dict[str, Any]:
        """Normalize API data field that may be a dict or JSON string."""
        data = response.get("data")
        if isinstance(data, dict):
            return data
        if isinstance(data, str):
            try:
                decoded = json.loads(data)
                if isinstance(decoded, dict):
                    return decoded
            except json.JSONDecodeError:
                return {}
        return {}

    @staticmethod
    def _extract_data(response: dict[str, Any]) -> Any:
        """Extract the response data field, decoding JSON strings when possible."""
        data = response.get("data")
        if isinstance(data, str):
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                return data
        return data

    async def get_system_list(self) -> list[dict[str, Any]]:
        """Fetch systems visible to the authenticated account.

        Returns:
            List of system objects. Returns an empty list on unexpected payload
            shapes.
        """
        response = await self._request(
            method="GET",
            path=self.paths.system_list,
            payload=None,
            include_bearer=True,
        )
        data = self._extract_data(response)
        return data if isinstance(data, list) else []

    async def get_operational_mode(self) -> dict[str, Any]:
        """Query the current operational mode for this system.

        Returns:
            Dict including parsed integer mode and official enum label.

        Raises:
            RuntimeError: If `system_id` is not set.
        """
        if not self.system_id:
            raise RuntimeError("system_id is not set.")

        query_path = self.paths.query_mode.replace("{systemId}", str(self.system_id))
        response = await self._request(
            method="GET",
            path=query_path,
            payload=None,
            include_bearer=True,
        )
        normalized_data = self._normalize_data(response)
        raw_data = response.get("data")

        mode: int | str | None
        if normalized_data:
            mode = normalized_data.get("energyStorageOperationMode")
        elif isinstance(raw_data, dict):
            mode = raw_data.get("energyStorageOperationMode")
        else:
            mode = raw_data

        if isinstance(mode, str) and mode.isdigit():
            mode = int(mode)

        mode_enum = OBSERVED_ACCOUNT_MODE_INT_TO_LABEL.get(
            mode,
            OFFICIAL_MODE_INT_TO_ENUM.get(mode, "UNKNOWN"),
        )
        return {
            "mode": mode,
            "label": mode_enum,
            "raw": response,
        }

    async def set_operational_mode(self, mode: int) -> dict[str, Any]:
        """Switch operational mode using integer mode values.

        Only officially documented mode integers are accepted here.

        Args:
            mode: Integer mode value.

        Returns:
            Result dictionary from the mode-switch operation.

        Raises:
            ValueError: If mode is not in supported official mode integers.
        """
        mode_enum = OFFICIAL_MODE_INT_TO_ENUM.get(mode)
        if mode_enum is None:
            supported = ", ".join(str(v) for v in sorted(OFFICIAL_MODE_INT_TO_ENUM))
            raise ValueError(
                f"Unsupported official mode '{mode}'. Supported mode integers: {supported}."
            )
        return await self.set_operational_mode_enum(mode_enum)

    async def set_operational_mode_enum(self, mode_enum: str) -> dict[str, Any]:
        """Switch operational mode using official enum values.

        Args:
            mode_enum: Official mode enum (MSC, FFG, NBI).

        Returns:
            Result dictionary including requested enum/int mode and raw response.

        Raises:
            RuntimeError: If `system_id` is not set.
            ValueError: If enum is unsupported.
        """
        if not self.system_id:
            raise RuntimeError("system_id is not set.")

        normalized = mode_enum.upper().strip()
        if normalized not in OFFICIAL_MODE_ENUM_TO_INT:
            raise ValueError(f"Unsupported official mode enum '{mode_enum}'.")

        payload = {
            "systemId": self.system_id,
            "energyStorageOperationMode": OFFICIAL_MODE_ENUM_TO_INT[normalized],
        }
        response = await self._request(
            method="PUT",
            path=self.paths.switch_mode,
            payload=payload,
            include_bearer=True,
        )
        return {
            "ok": True,
            "mode": OFFICIAL_MODE_ENUM_TO_INT[normalized],
            "mode_enum": normalized,
            "raw": response,
        }

    async def get_operational_modes(self) -> list[dict[str, str]]:
        """Return observed account-mode operating modes in compatible shape.

        Returns:
            List of dictionaries with `value` and `label` fields.
        """
        return self.operational_modes

    async def get_system_summary(self) -> dict[str, Any]:
        """Query system summary data from the official monitoring API.

        Returns:
            Parsed summary dictionary, or a raw wrapper when response shape differs.

        Raises:
            RuntimeError: If `system_id` is not set.
        """
        if not self.system_id:
            raise RuntimeError("system_id is not set.")

        path = self.paths.system_summary.replace("{systemId}", str(self.system_id))
        response = await self._request(
            method="GET",
            path=path,
            payload=None,
            include_bearer=True,
        )
        data = self._extract_data(response)
        return data if isinstance(data, dict) else {"raw": response}

    async def get_energy_flow(self) -> dict[str, Any]:
        """Query energy flow using configurable endpoint.

        Raises:
            RuntimeError: If energy flow path is not configured.
            RuntimeError: If `system_id` is not set.
        """
        if not self.paths.energy_flow:
            raise RuntimeError(
                "Official energy flow endpoint is not configured. Set SIGEN_OFFICIAL_ENERGY_FLOW_PATH."
            )
        if not self.system_id:
            raise RuntimeError("system_id is not set.")

        path = self.paths.energy_flow.replace("{systemId}", str(self.system_id))
        response = await self._request(
            method="GET",
            path=path,
            payload=None,
            include_bearer=True,
        )
        data = self._extract_data(response)
        return data if isinstance(data, dict) else {"raw": response}

    async def get_device_realtime(self, serial_number: str) -> dict[str, Any]:
        """Query realtime telemetry for a specific device serial number.

        Args:
            serial_number: Device serial number (for example inverter/AIO SN).

        Returns:
            Parsed realtime payload, or a raw wrapper when response shape differs.

        Raises:
            RuntimeError: If realtime path is not configured.
            RuntimeError: If `system_id` is not set.
            ValueError: If `serial_number` is empty.
        """
        if not self.paths.device_realtime:
            raise RuntimeError(
                "Official device realtime endpoint is not configured. "
                "Set SIGEN_OFFICIAL_DEVICE_REALTIME_PATH."
            )
        if not self.system_id:
            raise RuntimeError("system_id is not set.")
        if not serial_number.strip():
            raise ValueError("serial_number is required.")

        serial = serial_number.strip()
        candidate_paths = [
            self.paths.device_realtime,
            "/openapi/systems/{systemId}/device/realtime",
            "/openapi/systems/device/realtime",
            "/openapi/device/realtime",
        ]

        attempted: list[str] = []
        for candidate in dict.fromkeys(candidate_paths):
            if not candidate:
                continue

            resolved_path = candidate.replace("{systemId}", str(self.system_id))
            query_params = {"serialNumber": serial}
            if "{systemId}" not in candidate:
                query_params["systemId"] = str(self.system_id)

            try:
                response = await self._request(
                    method="GET",
                    path=resolved_path,
                    payload=None,
                    include_bearer=True,
                    query_params=query_params,
                )
                data = self._extract_data(response)
                return data if isinstance(data, dict) else {"raw": response}
            except RuntimeError as exc:
                attempted.append(f"{resolved_path} ? {query_params}")
                # Different tenants can expose different inventory/realtime paths.
                # Continue only for not-found style errors; otherwise surface immediately.
                if "failed (404)" in str(exc):
                    continue
                raise

        attempted_paths = " | ".join(attempted) if attempted else "none"
        raise RuntimeError(
            "Official device realtime endpoint not found for this tenant. "
            f"Attempted: {attempted_paths}. "
            "Also verify that serial_number is a DEVICE serial (not the systemId)."
        )