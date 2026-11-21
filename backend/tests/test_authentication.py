import json
import unittest

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.auth import (
    AUTH_CONFIG_ENV,
    TokenAuthenticator,
    encode_websocket_bearer_protocol,
    token_sha256,
)
from app.main import app


TOKEN = "test-operator-token"
TOKEN_CONFIG = json.dumps(
    [
        {
            "token_sha256": token_sha256(TOKEN),
            "subject": "operator-test",
            "role": "OPERATOR",
        }
    ]
)


class AuthenticationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.original_authenticator = app.state.token_authenticator
        app.state.token_authenticator = TokenAuthenticator.disabled()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.state.token_authenticator = self.original_authenticator

    def enable_authentication(self) -> None:
        app.state.token_authenticator = TokenAuthenticator.from_json(TOKEN_CONFIG)

    def test_authentication_is_disabled_without_configuration(self) -> None:
        response = self.client.get("/robots")
        status_response = self.client.get("/auth/me")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            status_response.json(),
            {"enabled": False, "subject": None, "role": None},
        )

    def test_protected_http_api_requires_valid_bearer_token(self) -> None:
        self.enable_authentication()

        missing = self.client.get("/robots")
        wrong_scheme = self.client.get(
            "/robots",
            headers={"Authorization": f"Basic {TOKEN}"},
        )
        wrong_token = self.client.get(
            "/robots",
            headers={"Authorization": "Bearer wrong-token"},
        )
        valid = self.client.get(
            "/robots",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

        self.assertEqual(missing.status_code, 401, missing.text)
        self.assertEqual(missing.headers["WWW-Authenticate"], "Bearer")
        self.assertEqual(wrong_scheme.status_code, 401, wrong_scheme.text)
        self.assertEqual(wrong_token.status_code, 401, wrong_token.text)
        self.assertEqual(valid.status_code, 200, valid.text)

    def test_health_and_console_are_public_when_authentication_is_enabled(self) -> None:
        self.enable_authentication()

        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/openapi.json").status_code, 200)
        self.assertEqual(self.client.get("/docs").status_code, 200)
        self.assertEqual(self.client.get("/console").status_code, 200)

    def test_auth_status_exposes_authenticated_subject_and_role(self) -> None:
        self.enable_authentication()

        missing = self.client.get("/auth/me")
        authenticated = self.client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

        self.assertEqual(missing.status_code, 401, missing.text)
        self.assertEqual(
            authenticated.json(),
            {
                "enabled": True,
                "subject": "operator-test",
                "role": "OPERATOR",
            },
        )

    def test_websocket_requires_token_when_authentication_is_enabled(self) -> None:
        self.enable_authentication()

        with self.assertRaises(WebSocketDisconnect) as raised:
            with self.client.websocket_connect("/ws/state"):
                pass
        self.assertEqual(raised.exception.code, 4401)

        with self.client.websocket_connect(
            "/ws/state",
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as websocket:
            snapshot = websocket.receive_json()
        self.assertEqual(snapshot["type"], "STATE_SNAPSHOT")

    def test_browser_websocket_can_use_encoded_subprotocol_token(self) -> None:
        self.enable_authentication()
        protocols = [
            "state.v1",
            encode_websocket_bearer_protocol(TOKEN),
        ]

        with self.client.websocket_connect(
            "/ws/state",
            subprotocols=protocols,
        ) as websocket:
            snapshot = websocket.receive_json()
            accepted_protocol = websocket.accepted_subprotocol

        self.assertEqual(accepted_protocol, "state.v1")
        self.assertEqual(snapshot["type"], "STATE_SNAPSHOT")

    def test_invalid_token_configuration_fails_closed(self) -> None:
        invalid_configs = (
            "not-json",
            "[]",
            json.dumps([{"token_sha256": "bad", "subject": "a", "role": "b"}]),
            json.dumps(
                [
                    {
                        "token_sha256": token_sha256(TOKEN),
                        "subject": "",
                        "role": "OPERATOR",
                    }
                ]
            ),
        )

        for raw_config in invalid_configs:
            with self.subTest(raw_config=raw_config):
                with self.assertRaisesRegex(ValueError, AUTH_CONFIG_ENV):
                    TokenAuthenticator.from_json(raw_config)

    def test_environment_loader_does_not_store_plaintext_token(self) -> None:
        authenticator = TokenAuthenticator.from_environment(
            lambda name: TOKEN_CONFIG if name == AUTH_CONFIG_ENV else None
        )

        self.assertTrue(authenticator.enabled)
        self.assertNotIn(TOKEN, repr(authenticator))
        self.assertEqual(
            authenticator.authenticate_token(TOKEN).subject,
            "operator-test",
        )


if __name__ == "__main__":
    unittest.main()
