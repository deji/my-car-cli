import unittest
from unittest.mock import MagicMock, patch
from my_car_cli.api import (
    CarAPIError,
    TokenExpiredException,
    exchange_intermediate_token,
    refresh_token,
)

# A realistic-shaping CIAS JWE: starts with "eyJ" and is well over 100 chars.
_FAKE_JWE = "eyJlbmMiOiJBMjU2Q0JDLUhTNTEyIiwiYWxnIjoiZGlyIn0.." + ("A" * 400)

# A canonical UUID-shaped intermediate token (the ?token=<uuid> from the CIAS
# callback redirect) — must NEVER be accepted as a bearer JWE.
_FAKE_UUID = "12345678-1234-4123-8123-123456789abc"


def _mock_client(mock_client_cls):
    """Return the inner client of a patched httpx.Client context manager."""
    return mock_client_cls.return_value.__enter__.return_value


def _mock_response(status_code, json_value=None, json_error=None):
    resp = MagicMock()
    resp.status_code = status_code
    if json_error is not None:
        resp.json.side_effect = json_error
    else:
        resp.json.return_value = json_value
    return resp


class TestRefreshToken(unittest.TestCase):
    """Tests for refresh_token() — mock httpx, no socket is opened."""

    @patch("my_car_cli.api.httpx.Client")
    def test_refresh_token_success_returns_new_jwe(self, mock_client_cls):
        client = _mock_client(mock_client_cls)
        client.post.return_value = _mock_response(200, {"jwe": _FAKE_JWE})

        result = refresh_token(_FAKE_JWE)

        self.assertEqual(result, _FAKE_JWE)
        client.post.assert_called_once()
        url = client.post.call_args.args[0]
        headers = client.post.call_args.kwargs["headers"]
        self.assertEqual(url, "https://api.oneweb.mercedes-benz.com/cias/v1/jwe/refresh")
        self.assertEqual(headers["Authorization"], f"Bearer {_FAKE_JWE}")
        self.assertEqual(headers["tenantId"], "oneweb")
        self.assertEqual(headers["Accept"], "application/json")

    @patch("my_car_cli.api.httpx.Client")
    def test_refresh_token_401_raises_token_expired(self, mock_client_cls):
        client = _mock_client(mock_client_cls)
        client.post.return_value = _mock_response(401, {"error": "unauthorized"})

        with self.assertRaises(TokenExpiredException):
            refresh_token(_FAKE_JWE)

    @patch("my_car_cli.api.httpx.Client")
    def test_refresh_token_non_jwe_payload_raises_car_api_error(self, mock_client_cls):
        client = _mock_client(mock_client_cls)
        client.post.return_value = _mock_response(200, {"jwe": "not-a-jwe"})

        with self.assertRaises(CarAPIError) as ctx:
            refresh_token(_FAKE_JWE)

        self.assertIn("200", str(ctx.exception))
        self.assertNotIn("not-a-jwe", str(ctx.exception))

    @patch("my_car_cli.api.httpx.Client")
    def test_refresh_token_other_status_raises_car_api_error(self, mock_client_cls):
        client = _mock_client(mock_client_cls)
        client.post.return_value = _mock_response(500, {"error": "boom"})

        with self.assertRaises(CarAPIError) as ctx:
            refresh_token(_FAKE_JWE)

        self.assertIn("500", str(ctx.exception))

    @patch("my_car_cli.api.httpx.Client")
    def test_refresh_token_non_json_body_raises_car_api_error(self, mock_client_cls):
        client = _mock_client(mock_client_cls)
        client.post.return_value = _mock_response(200, json_error=ValueError("not json"))

        with self.assertRaises(CarAPIError) as ctx:
            refresh_token(_FAKE_JWE)

        self.assertIn("200", str(ctx.exception))


class TestExchangeIntermediateToken(unittest.TestCase):
    """Tests for exchange_intermediate_token() — mock httpx, no socket is opened."""

    @patch("my_car_cli.api.httpx.Client")
    def test_exchange_success_returns_jwe(self, mock_client_cls):
        client = _mock_client(mock_client_cls)
        client.get.return_value = _mock_response(200, {"jwe": _FAKE_JWE})

        result = exchange_intermediate_token(_FAKE_UUID)

        self.assertEqual(result, _FAKE_JWE)
        client.get.assert_called_once()

    @patch("my_car_cli.api.httpx.Client")
    def test_exchange_accepts_raw_jwe_body(self, mock_client_cls):
        client = _mock_client(mock_client_cls)
        resp = _mock_response(200, json_error=ValueError("not json"))
        resp.text = _FAKE_JWE
        client.get.return_value = resp

        self.assertEqual(exchange_intermediate_token(_FAKE_UUID), _FAKE_JWE)
        url = client.get.call_args.args[0]
        headers = client.get.call_args.kwargs["headers"]
        self.assertEqual(url, f"https://api.oneweb.mercedes-benz.com/cias/v1/jwe/{_FAKE_UUID}")
        self.assertEqual(headers["tenantId"], "oneweb")
        self.assertEqual(headers["Accept"], "application/json")
        self.assertNotIn("Authorization", headers)

    @patch("my_car_cli.api.httpx.Client")
    def test_exchange_rejects_non_uuid_without_http_call(self, mock_client_cls):
        for bad in ("", _FAKE_JWE, "not-a-uuid", "12345678-1234-4123-8123-123456789ab", None):
            with self.assertRaises(CarAPIError):
                exchange_intermediate_token(bad)

        mock_client_cls.assert_not_called()

    @patch("my_car_cli.api.httpx.Client")
    def test_exchange_401_raises_token_expired(self, mock_client_cls):
        client = _mock_client(mock_client_cls)
        client.get.return_value = _mock_response(401, {"error": "unauthorized"})

        with self.assertRaises(TokenExpiredException):
            exchange_intermediate_token(_FAKE_UUID)

    @patch("my_car_cli.api.httpx.Client")
    def test_exchange_non_jwe_payload_raises_car_api_error(self, mock_client_cls):
        client = _mock_client(mock_client_cls)
        client.get.return_value = _mock_response(200, {"jwe": "not-a-jwe"})

        with self.assertRaises(CarAPIError) as ctx:
            exchange_intermediate_token(_FAKE_UUID)

        self.assertIn("200", str(ctx.exception))
        self.assertNotIn("not-a-jwe", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
