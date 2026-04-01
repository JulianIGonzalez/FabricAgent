"""
src/fabric_agent/fabric_client.py
==================================
Low-level REST client for the Microsoft Fabric API (v1).

Official API reference:
    https://learn.microsoft.com/en-us/rest/api/fabric/core/workspaces

Design decisions
────────────────
• One FabricClient instance per token lifetime. Tokens are valid ~60 min.
• Automatic pagination: Fabric returns a ``continuationToken`` / ``continuationUri``
  when there are more items. All list_* methods exhaust pagination transparently.
• Raises FabricAPIError for any non-2xx HTTP response so callers can handle
  errors uniformly without parsing raw responses.
• Keeps a single requests.Session for connection pooling (important in agents
  that make many sequential API calls).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
DEFAULT_API_BASE_URL = "https://api.fabric.microsoft.com/v1"
REQUEST_TIMEOUT_SECONDS = 30

# Fabric workspace types (as returned by the API)
WORKSPACE_TYPE_PERSONAL = "Personal"
WORKSPACE_TYPE_WORKSPACE = "Workspace"  # standard collaborative workspace


# ── Custom exception ──────────────────────────────────────────────────────────

class FabricAPIError(Exception):
    """Raised when the Fabric REST API returns a non-2xx status code."""

    def __init__(self, status_code: int, message: str, request_id: str = "") -> None:
        self.status_code = status_code
        self.request_id = request_id
        super().__init__(
            f"Fabric API error {status_code}: {message}"
            + (f" (x-ms-request-id: {request_id})" if request_id else "")
        )


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class Workspace:
    """Represents a single Microsoft Fabric workspace."""

    id: str
    display_name: str
    description: str = ""
    type: str = ""
    state: str = ""
    capacity_id: str = ""

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "Workspace":
        """Parse a workspace dict from the API JSON payload."""
        return cls(
            id=data.get("id", ""),
            display_name=data.get("displayName", ""),
            description=data.get("description", ""),
            type=data.get("type", ""),
            state=data.get("state", ""),
            capacity_id=data.get("capacityId", ""),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "displayName": self.display_name,
            "description": self.description,
            "type": self.type,
            "state": self.state,
            "capacityId": self.capacity_id,
        }

    def __str__(self) -> str:
        capacity_info = f" | capacity: {self.capacity_id}" if self.capacity_id else ""
        return (
            f"[{self.type:9s}] {self.display_name}"
            f" (id: {self.id}, state: {self.state}{capacity_info})"
        )


# ── Client ────────────────────────────────────────────────────────────────────

class FabricClient:
    """
    HTTP client for the Microsoft Fabric REST API v1.

    Usage
    ─────
    ::

        from fabric_agent.auth import get_fabric_token
        from fabric_agent.fabric_client import FabricClient

        token = get_fabric_token()               # picks up env-vars automatically
        client = FabricClient(token)
        workspaces = client.list_workspaces()    # returns list[Workspace]

    Args:
        access_token: A valid OAuth 2.0 bearer token for the Fabric API.
        base_url:     Override the API base URL (optional, for testing / future versions).
    """

    def __init__(
        self,
        access_token: str,
        base_url: str | None = None,
    ) -> None:
        self._base_url = (
            base_url
            or os.environ.get("FABRIC_API_BASE_URL", DEFAULT_API_BASE_URL)
        ).rstrip("/")

        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                # Useful for correlating requests in Fabric/Azure diagnostics
                "User-Agent": "FabricCopilotAgent/1.0 (python-requests)",
            }
        )
        logger.debug("FabricClient initialised with base URL: %s", self._base_url)

    # ── Public API methods ────────────────────────────────────────────────────

    def list_workspaces(self) -> list[Workspace]:
        """
        List all workspaces the authenticated principal can access.

        API endpoint:
            GET /v1/workspaces

        Reference:
            https://learn.microsoft.com/en-us/rest/api/fabric/core/workspaces/list-workspaces

        Behaviour:
            • For a **Service Principal**, returns workspaces where the SP has
              an explicit role (Admin / Member / Contributor / Viewer).
            • For a **delegated (user) token**, returns all workspaces the
              signed-in user has access to, including their My Workspace.
            • Automatically follows continuation tokens (pagination).

        Returns:
            list[Workspace]: Possibly empty if the principal has no workspace access.
        """
        workspaces: list[Workspace] = []
        page = 0

        for page_data in self._paginate("GET", "/workspaces"):
            page += 1
            items = page_data.get("value", [])
            logger.debug("Page %d: received %d workspace(s).", page, len(items))
            workspaces.extend(Workspace.from_api_response(item) for item in items)

        logger.info("Total workspaces retrieved: %d", len(workspaces))
        return workspaces

    def get_workspace(self, workspace_id: str) -> Workspace:
        """
        Get details of a single workspace by its ID.

        API endpoint:
            GET /v1/workspaces/{workspaceId}

        Args:
            workspace_id: The GUID of the workspace.

        Returns:
            Workspace: The workspace details.
        """
        data = self._get(f"/workspaces/{workspace_id}")
        return Workspace.from_api_response(data)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get(self, path: str, params: dict | None = None) -> dict:
        """Execute a single GET request and return the parsed JSON body."""
        url = self._base_url + path
        logger.debug("GET %s params=%s", url, params)

        response = self._session.get(
            url, params=params, timeout=REQUEST_TIMEOUT_SECONDS
        )
        self._raise_for_status(response)
        return response.json()

    def _paginate(
        self, method: str, path: str, params: dict | None = None
    ) -> Iterator[dict]:
        """
        Yield successive pages from a paginated Fabric list endpoint.

        Fabric uses a ``continuationUri`` field in the response body to point
        to the next page — unlike many APIs that use Link headers.
        """
        url: str | None = self._base_url + path
        page_params = dict(params or {})

        while url:
            logger.debug("%s %s params=%s", method, url, page_params)

            response = self._session.request(
                method,
                url,
                params=page_params if page_params else None,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            self._raise_for_status(response)
            body = response.json()
            yield body

            # Fabric pagination: follow continuationUri if present
            url = body.get("continuationUri")
            page_params = {}  # params are already encoded in the continuationUri

    @staticmethod
    def _raise_for_status(response: requests.Response) -> None:
        """
        Translate HTTP errors into FabricAPIError with actionable messages.

        Common Fabric error codes and their meaning:
            400 Bad Request    – malformed request (check query params / body)
            401 Unauthorized   – invalid or expired token; re-authenticate
            403 Forbidden      – principal lacks the required Fabric permission
            404 Not Found      – resource does not exist or is not visible to principal
            429 Too Many Reqs  – capacity throttling; back off and retry
            503 Service Unavail– transient Fabric/backend issue; retry with backoff
        """
        if response.ok:
            return

        request_id = response.headers.get("x-ms-request-id", "")
        try:
            body = response.json()
            error_code = body.get("errorCode", body.get("error", {}).get("code", ""))
            message = body.get("message", body.get("error", {}).get("message", response.text))
        except Exception:
            error_code = ""
            message = response.text

        # Enrich common errors with actionable hints
        hints = {
            401: "Token is missing or expired — call get_fabric_token() again.",
            403: (
                "Access denied. For service principals, ensure the SP is added "
                "as a Workspace member (Admin/Member/Contributor/Viewer), "
                "or holds the Fabric/Power Platform Administrator Entra role."
            ),
            429: "Fabric API rate limit hit. Implement exponential back-off and retry.",
        }
        hint = hints.get(response.status_code, "")
        full_message = f"{error_code}: {message}" if error_code else message
        if hint:
            full_message += f"\nHint: {hint}"

        raise FabricAPIError(
            status_code=response.status_code,
            message=full_message,
            request_id=request_id,
        )
