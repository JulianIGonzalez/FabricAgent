"""
src/fabric_agent/list_workspaces.py
=====================================
Runnable script: authenticate with Microsoft Fabric and list all workspaces
the principal has access to.

Usage
─────
1. Copy .env.example to .env and fill in your credentials.
2. Run:
       python -m fabric_agent.list_workspaces
   or:
       python src/fabric_agent/list_workspaces.py

Output
──────
Prints a formatted table of workspaces to stdout.  Set LOG_LEVEL=DEBUG for
detailed auth + HTTP tracing.

Environment variables
─────────────────────
See .env.example for the full list with descriptions.
"""

import json
import logging
import os
import sys

# Load .env before anything else so all os.environ lookups pick up local values.
# In CI / production the vars are injected by the platform (GitHub Secrets, etc.)
# and python-dotenv simply skips loading the (absent) .env file — safe either way.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional; real envs inject vars directly

from fabric_agent.auth import get_fabric_token
from fabric_agent.fabric_client import FabricClient, FabricAPIError, Workspace

# ── Logging setup ─────────────────────────────────────────────────────────────
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Main function ─────────────────────────────────────────────────────────────

def list_workspaces_for_principal(output_format: str = "table") -> list[dict]:
    """
    Authenticate and retrieve all workspaces accessible to the current principal.

    This is the function GitHub Copilot agents (or any other caller) should
    import and invoke.

    Args:
        output_format: ``"table"`` (default, human-readable) or ``"json"``
                       (machine-readable, useful when consumed by the Copilot agent).

    Returns:
        list[dict]: A list of workspace dicts, each with keys:
                    id, displayName, description, type, state, capacityId.

    Raises:
        EnvironmentError: If required env-vars are missing.
        RuntimeError:     If authentication fails.
        FabricAPIError:   If the Fabric API returns an error response.
    """
    # 1. Authenticate ──────────────────────────────────────────────────────────
    logger.info("Authenticating with Microsoft Fabric …")
    token = get_fabric_token()
    logger.info("Authentication successful.")

    # 2. Call the API ──────────────────────────────────────────────────────────
    client = FabricClient(token)
    logger.info("Fetching workspaces from Fabric API …")
    workspaces: list[Workspace] = client.list_workspaces()

    # 3. Render output ─────────────────────────────────────────────────────────
    result = [ws.to_dict() for ws in workspaces]

    if output_format == "json":
        print(json.dumps(result, indent=2))
    else:
        _print_table(workspaces)

    return result


def _print_table(workspaces: list[Workspace]) -> None:
    """Print workspaces as a formatted ASCII table."""
    if not workspaces:
        print("\n⚠️  No workspaces found for this principal.\n")
        print(
            "Possible reasons:\n"
            "  • Service principal is not a member of any workspace.\n"
            "  • User account has no Fabric workspaces.\n"
            "  • The token was issued for the wrong tenant.\n"
        )
        return

    # Column widths (dynamic, capped for readability)
    col_name   = min(max(len(ws.display_name) for ws in workspaces), 50)
    col_type   = max(len(ws.type) for ws in workspaces)
    col_state  = max(len(ws.state) for ws in workspaces)
    col_id     = 36  # GUIDs are always 36 chars

    header = (
        f"{'Workspace Name':<{col_name}}  "
        f"{'Type':<{col_type}}  "
        f"{'State':<{col_state}}  "
        f"{'ID':<{col_id}}  "
        f"Capacity ID"
    )
    separator = "─" * len(header)

    print(f"\n{'─'*10}  Microsoft Fabric Workspaces  {'─'*10}")
    print(f"Total: {len(workspaces)} workspace(s)\n")
    print(header)
    print(separator)

    for ws in workspaces:
        name = ws.display_name[:col_name] if len(ws.display_name) > col_name else ws.display_name
        print(
            f"{name:<{col_name}}  "
            f"{ws.type:<{col_type}}  "
            f"{ws.state:<{col_state}}  "
            f"{ws.id:<{col_id}}  "
            f"{ws.capacity_id or '—'}"
        )

    print(separator)
    print()


# ── CLI entrypoint ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Accept an optional --json flag for machine-readable output
    fmt = "json" if "--json" in sys.argv else "table"

    try:
        list_workspaces_for_principal(output_format=fmt)
    except EnvironmentError as exc:
        logger.error("Configuration error: %s", exc)
        sys.exit(1)
    except RuntimeError as exc:
        logger.error("Authentication error: %s", exc)
        sys.exit(2)
    except FabricAPIError as exc:
        logger.error("Fabric API error: %s", exc)
        sys.exit(3)
    except KeyboardInterrupt:
        print("\nCancelled by user.")
        sys.exit(0)
