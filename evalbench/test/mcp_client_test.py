"""Unit tests for mcp_client auth and scope extraction."""

import unittest
from unittest.mock import MagicMock, patch

from generators.models.mcp_client import (
    McpToolsError,
    auth_headers,
    extract_mcp_oauth_scopes,
)


class ExtractMcpOauthScopesTest(unittest.TestCase):

    def test_canonical_schema(self):
        config = {
            "oauth": {
                "scopes": [
                    "https://www.googleapis.com/auth/cloud-platform",
                    "https://www.googleapis.com/auth/dfareporting",
                ]
            }
        }
        self.assertEqual(
            extract_mcp_oauth_scopes(config),
            [
                "https://www.googleapis.com/auth/cloud-platform",
                "https://www.googleapis.com/auth/dfareporting",
            ],
        )

    def test_strips_whitespace_and_drops_empty(self):
        config = {
            "oauth": {
                "scopes": [
                    "  https://www.googleapis.com/auth/cloud-platform  ",
                    "",
                    "   ",
                    "https://www.googleapis.com/auth/dfareporting",
                ]
            }
        }
        self.assertEqual(
            extract_mcp_oauth_scopes(config),
            [
                "https://www.googleapis.com/auth/cloud-platform",
                "https://www.googleapis.com/auth/dfareporting",
            ],
        )

    def test_missing_or_none_oauth(self):
        self.assertEqual(extract_mcp_oauth_scopes({}), [])
        self.assertEqual(extract_mcp_oauth_scopes({"oauth": None}), [])

    def test_non_dict_oauth_does_not_raise(self):
        self.assertEqual(
            extract_mcp_oauth_scopes({"oauth": ["https://www.googleapis.com/auth/cloud-platform"]}),
            [],
        )
        self.assertEqual(
            extract_mcp_oauth_scopes({"oauth": "https://www.googleapis.com/auth/cloud-platform"}),
            [],
        )
        self.assertEqual(extract_mcp_oauth_scopes({"oauth": 123}), [])

    def test_missing_or_non_list_scopes_does_not_raise(self):
        self.assertEqual(extract_mcp_oauth_scopes({"oauth": {}}), [])
        self.assertEqual(extract_mcp_oauth_scopes({"oauth": {"scopes": None}}), [])
        self.assertEqual(
            extract_mcp_oauth_scopes({"oauth": {"scopes": "https://www.googleapis.com/auth/cloud-platform"}}),
            [],
        )

    def test_top_level_scopes_ignored(self):
        self.assertEqual(
            extract_mcp_oauth_scopes({"scopes": ["https://www.googleapis.com/auth/cloud-platform"]}),
            [],
        )


class AuthHeadersTest(unittest.TestCase):

    def test_no_auth_provider_returns_none_if_no_headers(self):
        self.assertIsNone(auth_headers({}))

    def test_no_auth_provider_preserves_static_headers(self):
        self.assertEqual(
            auth_headers({"headers": {"X-Custom": "val"}}),
            {"X-Custom": "val"},
        )

    def test_google_credentials_missing_scopes_raises_mcp_tools_error(self):
        with self.assertRaises(McpToolsError) as ctx:
            auth_headers({"authProviderType": "google_credentials"})
        self.assertIn("requires oauth.scopes", str(ctx.exception))

    def test_google_credentials_non_dict_oauth_raises_mcp_tools_error(self):
        with self.assertRaises(McpToolsError) as ctx:
            auth_headers({
                "authProviderType": "google_credentials",
                "oauth": ["https://www.googleapis.com/auth/cloud-platform"],
            })
        self.assertIn("requires oauth.scopes", str(ctx.exception))

    @patch("google.auth.default", side_effect=Exception("network error"))
    def test_google_credentials_adc_failure_raises_mcp_tools_error(self, mock_default):
        with self.assertRaises(McpToolsError) as ctx:
            auth_headers({
                "authProviderType": "google_credentials",
                "oauth": {"scopes": ["https://www.googleapis.com/auth/cloud-platform"]},
            })
        self.assertIn("Failed to acquire GCP Application Default Credentials", str(ctx.exception))

    @patch("google.auth.default")
    @patch("google.auth.transport.requests.Request")
    def test_google_credentials_fetches_token(self, mock_request, mock_default):
        mock_creds = MagicMock()
        mock_creds.token = "token123"
        mock_default.return_value = (mock_creds, "project1")

        headers = auth_headers({
            "authProviderType": "google_credentials",
            "headers": {"X-Goog-User-Project": "project1"},
            "oauth": {
                "scopes": ["https://www.googleapis.com/auth/cloud-platform"]
            },
        })
        mock_default.assert_called_once_with(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        self.assertEqual(headers["Authorization"], "Bearer token123")
        self.assertEqual(headers["X-Goog-User-Project"], "project1")


if __name__ == "__main__":
    unittest.main()
