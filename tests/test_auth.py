"""
tests/test_auth.py
==================
Unit tests for the authentication module (fabric_agent.auth).

All tests are fully offline — no real Azure AD calls are made.
MSAL is patched at the module level.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fabric_agent.auth import (
    get_fabric_token,
    get_token_device_code,
    get_token_service_principal,
    _validate_token_result,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

GOOD_ENV = {
    "FABRIC_TENANT_ID": "tenant-123",
    "FABRIC_CLIENT_ID": "client-456",
    "FABRIC_CLIENT_SECRET": "super-secret",
    "FABRIC_AUTH_FLOW": "service_principal",
}

GOOD_TOKEN_RESULT = {"access_token": "mock-access-token-xyz"}


# ── Service Principal tests ───────────────────────────────────────────────────

class TestGetTokenServicePrincipal(unittest.TestCase):

    @patch("fabric_agent.auth.msal.ConfidentialClientApplication")
    def test_returns_token_on_success(self, mock_app_cls):
        mock_app = MagicMock()
        mock_app.acquire_token_for_client.return_value = GOOD_TOKEN_RESULT
        mock_app_cls.return_value = mock_app

        with patch.dict(os.environ, GOOD_ENV, clear=True):
            token = get_token_service_principal()

        self.assertEqual(token, "mock-access-token-xyz")
        mock_app.acquire_token_for_client.assert_called_once_with(
            scopes=["https://api.fabric.microsoft.com/.default"]
        )

    @patch("fabric_agent.auth.msal.ConfidentialClientApplication")
    def test_raises_on_msal_error(self, mock_app_cls):
        mock_app = MagicMock()
        mock_app.acquire_token_for_client.return_value = {
            "error": "invalid_client",
            "error_description": "Bad credentials",
        }
        mock_app_cls.return_value = mock_app

        with patch.dict(os.environ, GOOD_ENV, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                get_token_service_principal()

        self.assertIn("invalid_client", str(ctx.exception))
        self.assertIn("Bad credentials", str(ctx.exception))

    def test_raises_on_missing_tenant_id(self):
        env = {k: v for k, v in GOOD_ENV.items() if k != "FABRIC_TENANT_ID"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(EnvironmentError) as ctx:
                get_token_service_principal()
        self.assertIn("FABRIC_TENANT_ID", str(ctx.exception))

    def test_raises_on_missing_client_secret(self):
        env = {k: v for k, v in GOOD_ENV.items() if k != "FABRIC_CLIENT_SECRET"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(EnvironmentError) as ctx:
                get_token_service_principal()
        self.assertIn("FABRIC_CLIENT_SECRET", str(ctx.exception))


# ── Device Code tests ─────────────────────────────────────────────────────────

class TestGetTokenDeviceCode(unittest.TestCase):

    @patch("builtins.print")  # suppress the user-facing message in tests
    @patch("fabric_agent.auth.msal.PublicClientApplication")
    def test_returns_token_on_success(self, mock_app_cls, _mock_print):
        mock_app = MagicMock()
        mock_app.initiate_device_flow.return_value = {
            "user_code": "ABC123",
            "message": "Go to https://microsoft.com/devicelogin and enter ABC123",
        }
        mock_app.acquire_token_by_device_flow.return_value = GOOD_TOKEN_RESULT
        mock_app_cls.return_value = mock_app

        env = {"FABRIC_TENANT_ID": "tenant-123", "FABRIC_CLIENT_ID": "client-456"}
        with patch.dict(os.environ, env, clear=True):
            token = get_token_device_code()

        self.assertEqual(token, "mock-access-token-xyz")

    @patch("fabric_agent.auth.msal.PublicClientApplication")
    def test_raises_when_device_flow_fails_to_initiate(self, mock_app_cls):
        mock_app = MagicMock()
        mock_app.initiate_device_flow.return_value = {
            "error": "unauthorized_client",
        }
        mock_app_cls.return_value = mock_app

        env = {"FABRIC_TENANT_ID": "tenant-123", "FABRIC_CLIENT_ID": "client-456"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                get_token_device_code()
        self.assertIn("initiate device code flow", str(ctx.exception))


# ── Unified get_fabric_token dispatcher ───────────────────────────────────────

class TestGetFabricToken(unittest.TestCase):

    @patch("fabric_agent.auth.get_token_service_principal", return_value="sp-token")
    def test_selects_service_principal_by_env(self, mock_sp):
        with patch.dict(os.environ, {"FABRIC_AUTH_FLOW": "service_principal"}, clear=False):
            token = get_fabric_token()
        self.assertEqual(token, "sp-token")
        mock_sp.assert_called_once()

    @patch("fabric_agent.auth.get_token_device_code", return_value="dc-token")
    def test_selects_device_code_by_env(self, mock_dc):
        with patch.dict(os.environ, {"FABRIC_AUTH_FLOW": "device_code"}, clear=False):
            token = get_fabric_token()
        self.assertEqual(token, "dc-token")
        mock_dc.assert_called_once()

    @patch("fabric_agent.auth.get_token_service_principal", return_value="sp-token")
    def test_parameter_overrides_env(self, mock_sp):
        with patch.dict(os.environ, {"FABRIC_AUTH_FLOW": "device_code"}, clear=False):
            token = get_fabric_token(auth_flow="service_principal")
        self.assertEqual(token, "sp-token")

    def test_raises_on_unknown_flow(self):
        with self.assertRaises(ValueError) as ctx:
            get_fabric_token(auth_flow="oauth_magic")
        self.assertIn("oauth_magic", str(ctx.exception))

    @patch("fabric_agent.auth.get_token_service_principal", return_value="default-token")
    def test_defaults_to_service_principal_when_env_unset(self, mock_sp):
        env_without_flow = {k: v for k, v in os.environ.items() if k != "FABRIC_AUTH_FLOW"}
        with patch.dict(os.environ, env_without_flow, clear=True):
            token = get_fabric_token()
        self.assertEqual(token, "default-token")


# ── _validate_token_result ────────────────────────────────────────────────────

class TestValidateTokenResult(unittest.TestCase):

    def test_passes_silently_for_valid_result(self):
        _validate_token_result({"access_token": "tok"}, flow="test")  # no exception

    def test_raises_with_error_details(self):
        with self.assertRaises(RuntimeError) as ctx:
            _validate_token_result(
                {"error": "invalid_grant", "error_description": "Token expired."},
                flow="service_principal",
            )
        msg = str(ctx.exception)
        self.assertIn("invalid_grant", msg)
        self.assertIn("Token expired.", msg)
        self.assertIn("service_principal", msg)


if __name__ == "__main__":
    unittest.main()
