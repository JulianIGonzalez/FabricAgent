"""
tests/test_fabric_client.py
============================
Unit tests for FabricClient and the Workspace data model.

All HTTP calls are intercepted with unittest.mock — no real network traffic.
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fabric_agent.fabric_client import (
    FabricClient,
    FabricAPIError,
    Workspace,
    DEFAULT_API_BASE_URL,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_response(status_code: int, body: dict, headers: dict | None = None) -> MagicMock:
    """Create a mock requests.Response with the given status and JSON body."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = status_code < 400
    resp.json.return_value = body
    resp.text = json.dumps(body)
    resp.headers = headers or {}
    return resp


WORKSPACE_1 = {
    "id": "ws-id-001",
    "displayName": "Data Engineering DEV",
    "description": "Development workspace",
    "type": "Workspace",
    "state": "Active",
    "capacityId": "cap-id-abc",
}

WORKSPACE_2 = {
    "id": "ws-id-002",
    "displayName": "My Workspace",
    "description": "",
    "type": "Personal",
    "state": "Active",
    "capacityId": "",
}


# ── Workspace model ───────────────────────────────────────────────────────────

class TestWorkspaceModel(unittest.TestCase):

    def test_from_api_response_all_fields(self):
        ws = Workspace.from_api_response(WORKSPACE_1)
        self.assertEqual(ws.id, "ws-id-001")
        self.assertEqual(ws.display_name, "Data Engineering DEV")
        self.assertEqual(ws.description, "Development workspace")
        self.assertEqual(ws.type, "Workspace")
        self.assertEqual(ws.state, "Active")
        self.assertEqual(ws.capacity_id, "cap-id-abc")

    def test_from_api_response_missing_optional_fields(self):
        ws = Workspace.from_api_response({"id": "x", "displayName": "Min"})
        self.assertEqual(ws.id, "x")
        self.assertEqual(ws.description, "")
        self.assertEqual(ws.capacity_id, "")

    def test_to_dict_round_trip(self):
        ws = Workspace.from_api_response(WORKSPACE_1)
        d = ws.to_dict()
        self.assertEqual(d["id"], WORKSPACE_1["id"])
        self.assertEqual(d["displayName"], WORKSPACE_1["displayName"])
        self.assertEqual(d["capacityId"], WORKSPACE_1["capacityId"])

    def test_str_representation_includes_name_and_state(self):
        ws = Workspace.from_api_response(WORKSPACE_1)
        result = str(ws)
        self.assertIn("Data Engineering DEV", result)
        self.assertIn("Active", result)
        self.assertIn("cap-id-abc", result)

    def test_str_representation_no_capacity(self):
        ws = Workspace.from_api_response(WORKSPACE_2)
        result = str(ws)
        self.assertNotIn("capacity:", result)


# ── FabricClient.list_workspaces ──────────────────────────────────────────────

class TestListWorkspaces(unittest.TestCase):

    def _make_client(self) -> FabricClient:
        return FabricClient(access_token="mock-token")

    def test_single_page_returns_all_workspaces(self):
        client = self._make_client()
        page_response = _make_response(200, {"value": [WORKSPACE_1, WORKSPACE_2]})

        with patch.object(client._session, "request", return_value=page_response):
            workspaces = client.list_workspaces()

        self.assertEqual(len(workspaces), 2)
        self.assertEqual(workspaces[0].id, "ws-id-001")
        self.assertEqual(workspaces[1].id, "ws-id-002")

    def test_pagination_follows_continuation_uri(self):
        client = self._make_client()

        page1 = _make_response(200, {
            "value": [WORKSPACE_1],
            "continuationUri": f"{DEFAULT_API_BASE_URL}/workspaces?continuationToken=tok2",
            "continuationToken": "tok2",
        })
        page2 = _make_response(200, {"value": [WORKSPACE_2]})

        with patch.object(client._session, "request", side_effect=[page1, page2]):
            workspaces = client.list_workspaces()

        self.assertEqual(len(workspaces), 2)

    def test_empty_response_returns_empty_list(self):
        client = self._make_client()
        page_response = _make_response(200, {"value": []})

        with patch.object(client._session, "request", return_value=page_response):
            workspaces = client.list_workspaces()

        self.assertEqual(workspaces, [])

    def test_calls_correct_endpoint(self):
        client = self._make_client()
        page_response = _make_response(200, {"value": []})

        with patch.object(client._session, "request", return_value=page_response) as mock_req:
            client.list_workspaces()

        call_args = mock_req.call_args
        self.assertIn("/workspaces", call_args[0][1])  # URL positional arg

    def test_authorization_header_is_set(self):
        client = self._make_client()
        self.assertIn("Authorization", client._session.headers)
        self.assertEqual(client._session.headers["Authorization"], "Bearer mock-token")


# ── FabricClient.get_workspace ────────────────────────────────────────────────

class TestGetWorkspace(unittest.TestCase):

    def test_returns_single_workspace(self):
        client = FabricClient(access_token="mock-token")
        response = _make_response(200, WORKSPACE_1)

        with patch.object(client._session, "get", return_value=response):
            ws = client.get_workspace("ws-id-001")

        self.assertEqual(ws.id, "ws-id-001")
        self.assertEqual(ws.display_name, "Data Engineering DEV")


# ── Error handling ────────────────────────────────────────────────────────────

class TestErrorHandling(unittest.TestCase):

    def _make_client(self) -> FabricClient:
        return FabricClient(access_token="mock-token")

    def test_401_raises_fabric_api_error(self):
        client = self._make_client()
        resp = _make_response(401, {"message": "Token expired"})

        with patch.object(client._session, "request", return_value=resp):
            with self.assertRaises(FabricAPIError) as ctx:
                client.list_workspaces()

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("Token", str(ctx.exception))

    def test_403_raises_with_hint(self):
        client = self._make_client()
        resp = _make_response(403, {"message": "Forbidden"})

        with patch.object(client._session, "request", return_value=resp):
            with self.assertRaises(FabricAPIError) as ctx:
                client.list_workspaces()

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("403", str(ctx.exception))

    def test_429_raises_with_rate_limit_hint(self):
        client = self._make_client()
        resp = _make_response(429, {"message": "Too many requests"})

        with patch.object(client._session, "request", return_value=resp):
            with self.assertRaises(FabricAPIError) as ctx:
                client.list_workspaces()

        self.assertIn("rate limit", str(ctx.exception).lower())

    def test_request_id_included_in_error(self):
        client = self._make_client()
        resp = _make_response(
            500,
            {"message": "Internal error"},
            headers={"x-ms-request-id": "req-abc-123"},
        )

        with patch.object(client._session, "request", return_value=resp):
            with self.assertRaises(FabricAPIError) as ctx:
                client.list_workspaces()

        self.assertEqual(ctx.exception.request_id, "req-abc-123")
        self.assertIn("req-abc-123", str(ctx.exception))

    def test_non_json_error_body_handled_gracefully(self):
        client = self._make_client()
        resp = _make_response(503, {})
        resp.json.side_effect = ValueError("not json")
        resp.text = "Service Unavailable"

        with patch.object(client._session, "request", return_value=resp):
            with self.assertRaises(FabricAPIError) as ctx:
                client.list_workspaces()

        self.assertIn("503", str(ctx.exception))

    def test_custom_base_url_from_env(self):
        custom_url = "https://custom.fabric.api/v2"
        with patch.dict(os.environ, {"FABRIC_API_BASE_URL": custom_url}):
            client = FabricClient(access_token="tok")
        self.assertEqual(client._base_url, custom_url)

    def test_fabric_api_error_attributes(self):
        err = FabricAPIError(status_code=403, message="Denied", request_id="rid-1")
        self.assertEqual(err.status_code, 403)
        self.assertEqual(err.request_id, "rid-1")
        self.assertIn("403", str(err))
        self.assertIn("Denied", str(err))
        self.assertIn("rid-1", str(err))


if __name__ == "__main__":
    unittest.main()
