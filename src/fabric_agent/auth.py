"""
src/fabric_agent/auth.py
========================
Authentication helpers for Microsoft Fabric REST API.

Supports two flows controlled by the FABRIC_AUTH_FLOW env-var:

  service_principal (default / recommended for automation)
  ─────────────────────────────────────────────────────────
  Uses the OAuth 2.0 Client Credentials Grant.
  Requires: FABRIC_TENANT_ID, FABRIC_CLIENT_ID, FABRIC_CLIENT_SECRET.

  The Service Principal must have one of the following to see workspaces:
    • Be added as a Workspace member (Admin/Member/Contributor/Viewer), OR
    • Have the "Fabric Administrator" or "Power Platform Administrator"
      Entra ID role (to list ALL tenant workspaces via admin endpoints).

  device_code (interactive / delegated)
  ──────────────────────────────────────
  Launches the OAuth 2.0 Device Authorization Grant.
  The user opens a browser URL, enters a code, and authenticates.
  Returns a token scoped to that user's own accessible workspaces.
  Requires: FABRIC_TENANT_ID, FABRIC_CLIENT_ID.
  The Entra ID app must have "Delegated" Fabric API permissions
  and "Allow public client flows" enabled.
"""

import os
import logging
from typing import Optional

import msal

logger = logging.getLogger(__name__)

# ── Fabric API scope ──────────────────────────────────────────────────────────
# This single scope covers all Fabric REST API endpoints.
# For service-principal flows, the scope MUST end with /.default.
FABRIC_SCOPE_SP = "https://api.fabric.microsoft.com/.default"

# For delegated (user) flows you can request granular scopes; /.default works too.
FABRIC_SCOPE_DELEGATED = "https://api.fabric.microsoft.com/.default"

# Entra ID authority URL template
AUTHORITY_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}"


def _require_env(name: str) -> str:
    """Read a required environment variable or raise a clear error."""
    value = os.environ.get(name)
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{name}' is not set. "
            "Copy .env.example to .env and fill in real values."
        )
    return value


def get_token_service_principal() -> str:
    """
    Acquire a bearer token using the Client Credentials (Service Principal) flow.

    Environment variables consumed:
        FABRIC_TENANT_ID     – Azure AD tenant / directory ID
        FABRIC_CLIENT_ID     – App registration client ID
        FABRIC_CLIENT_SECRET – App registration client secret

    Returns:
        str: A valid bearer token string (no "Bearer " prefix).

    Raises:
        EnvironmentError: If any required env-var is missing.
        RuntimeError:     If MSAL returns an error response.
    """
    tenant_id = _require_env("FABRIC_TENANT_ID")
    client_id = _require_env("FABRIC_CLIENT_ID")
    client_secret = _require_env("FABRIC_CLIENT_SECRET")

    authority = AUTHORITY_TEMPLATE.format(tenant_id=tenant_id)

    # ConfidentialClientApplication caches tokens in memory automatically.
    # For long-running services consider a persistent token cache.
    app = msal.ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_secret,
        authority=authority,
    )

    logger.debug("Requesting token via Client Credentials (service principal) …")
    result = app.acquire_token_for_client(scopes=[FABRIC_SCOPE_SP])

    _validate_token_result(result, flow="service_principal")
    logger.debug("Token acquired successfully (service principal).")
    return result["access_token"]


def get_token_device_code() -> str:
    """
    Acquire a bearer token using the Device Code (interactive delegated) flow.

    The function prints instructions to stdout — the user visits a URL and enters
    a short code to authenticate in their browser.

    Environment variables consumed:
        FABRIC_TENANT_ID – Azure AD tenant / directory ID
        FABRIC_CLIENT_ID – App registration client ID (must allow public flows)

    Returns:
        str: A valid bearer token string (no "Bearer " prefix).

    Raises:
        EnvironmentError: If any required env-var is missing.
        RuntimeError:     If MSAL returns an error response.
    """
    tenant_id = _require_env("FABRIC_TENANT_ID")
    client_id = _require_env("FABRIC_CLIENT_ID")

    authority = AUTHORITY_TEMPLATE.format(tenant_id=tenant_id)

    # PublicClientApplication is used for delegated / interactive flows.
    app = msal.PublicClientApplication(
        client_id=client_id,
        authority=authority,
    )

    # Step 1: obtain the device code and user-facing message
    flow = app.initiate_device_flow(scopes=[FABRIC_SCOPE_DELEGATED])
    if "user_code" not in flow:
        raise RuntimeError(
            f"Failed to initiate device code flow: {flow.get('error_description', flow)}"
        )

    # Prompt the user
    print("\n" + "=" * 60)
    print("  ACTION REQUIRED — Microsoft Fabric Authentication")
    print("=" * 60)
    print(flow["message"])  # MSAL provides the full human-readable message
    print("=" * 60 + "\n")

    # Step 2: poll until the user completes authentication (blocking)
    logger.debug("Waiting for user to complete device-code authentication …")
    result = app.acquire_token_by_device_flow(flow)

    _validate_token_result(result, flow="device_code")
    logger.debug("Token acquired successfully (device code / delegated).")
    return result["access_token"]


def get_fabric_token(auth_flow: Optional[str] = None) -> str:
    """
    Unified entry point — selects the authentication flow automatically.

    Priority order for determining the flow:
        1. ``auth_flow`` parameter (if explicitly passed)
        2. ``FABRIC_AUTH_FLOW`` environment variable
        3. Defaults to ``"service_principal"``

    Args:
        auth_flow: Optional override. Either ``"service_principal"``
                   or ``"device_code"``.

    Returns:
        str: A valid bearer token for the Fabric API.
    """
    resolved_flow = (
        auth_flow
        or os.environ.get("FABRIC_AUTH_FLOW", "service_principal")
    ).strip().lower()

    logger.info("Using auth flow: %s", resolved_flow)

    if resolved_flow == "service_principal":
        return get_token_service_principal()
    elif resolved_flow == "device_code":
        return get_token_device_code()
    else:
        raise ValueError(
            f"Unknown FABRIC_AUTH_FLOW value: '{resolved_flow}'. "
            "Expected 'service_principal' or 'device_code'."
        )


# ── Internal helpers ──────────────────────────────────────────────────────────

def _validate_token_result(result: dict, flow: str) -> None:
    """Raise a descriptive RuntimeError if the MSAL result contains an error."""
    if "access_token" not in result:
        error = result.get("error", "unknown_error")
        description = result.get("error_description", "No description provided.")
        correlation_id = result.get("correlation_id", "N/A")
        raise RuntimeError(
            f"Authentication failed [{flow}].\n"
            f"  Error          : {error}\n"
            f"  Description    : {description}\n"
            f"  Correlation ID : {correlation_id}\n\n"
            "Troubleshooting tips:\n"
            "  • Verify FABRIC_TENANT_ID, FABRIC_CLIENT_ID, FABRIC_CLIENT_SECRET.\n"
            "  • Ensure the app registration has Fabric API permissions.\n"
            "  • For service_principal: check the client secret has not expired.\n"
            "  • For device_code: ensure 'Allow public client flows' is ON in Entra ID."
        )
