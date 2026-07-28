import json
import unittest
from unittest.mock import patch
from pathlib import Path
from my_car_cli.auth import (
    _looks_like_jwe,
    _extract_token_from_url,
    get_stored_token,
    set_stored_token,
    SERVICE_NAME,
    KEYRING_USER,
)
from my_car_cli.config import load_config, update_config_key
from my_car_cli.display import format_timestamp, km_to_miles, kpa_to_psi, render_dashboard

# A realistic-shaping CIAS JWE: starts with "eyJ" and is well over 100 chars.
_FAKE_JWE = "eyJlbmMiOiJBMjU2Q0JDLUhTNTEyIiwiYWxnIjoiZGlyIn0.." + ("A" * 400)


class TestMyCarCLI(unittest.TestCase):

    def test_unit_conversions(self):
        # 200 KPA -> ~29.0 PSI
        self.assertAlmostEqual(kpa_to_psi(200.0), 29.0, places=1)
        # 250 KPA -> ~36.3 PSI
        self.assertAlmostEqual(kpa_to_psi(250.0), 36.3, places=1)
        # 100 KM -> ~62 Miles
        self.assertEqual(km_to_miles(100), 62)

    def test_format_timestamp(self):
        # 1704067200000 ms -> 1 Jan 2024 (local time may cross the date boundary)
        ts_str = format_timestamp(1704067200000)
        self.assertIn("2024", ts_str)

    def test_render_dashboard(self):
        mock_status = {
            "useCase": "DISPLAY_LIVEDATA",
            "liveData": {
                "lastUpdateTimestamp": 1704067200000,
                "levels": [
                    {"value": 50, "type": "ELECTRIC", "unit": "PERCENTAGE", "displayLowFuelWarning": False}
                ],
                "mileage": {"value": 12345, "unit": "MILES"},
                "ranges": [
                    {"value": 100, "type": "ELECTRIC", "unit": "MILES", "displayLowRangeWarning": False}
                ],
                "tires": [
                    {"value": 280.0, "type": "FRONT_LEFT", "warning": "OK", "unit": "KPA"},
                    {"value": 280.0, "type": "FRONT_RIGHT", "warning": "OK", "unit": "KPA"},
                    {"value": 280.0, "type": "REAR_LEFT", "warning": "OK", "unit": "KPA"},
                    {"value": 280.0, "type": "REAR_RIGHT", "warning": "OK", "unit": "KPA"},
                ],
                "brakeFluid": {"fluidLevelWarning": False},
            },
        }

        mock_next_service = {
            "days": {},
            "mileage": {"remaining": 5000, "status": "OK", "limit": 60000, "unit": "KM"},
            "extent": "UNKNOWN",
            "status": {"value": "OK", "references": ["MILEAGE"]},
        }

        # Should render cleanly without throwing exceptions
        try:
            render_dashboard(mock_status, mock_next_service, is_cached=True)
        except Exception as e:
            self.fail(f"render_dashboard raised an exception: {e}")

    def test_config(self):
        cfg = load_config()
        self.assertIn("vin", cfg)
        updated = update_config_key("pressure_unit", "PSI")
        self.assertEqual(updated.get("pressure_unit"), "PSI")

    def test_looks_like_jwe(self):
        self.assertTrue(_looks_like_jwe(_FAKE_JWE))
        # Too short / wrong prefix -> rejected (prevents capturing bogus tokens)
        self.assertFalse(_looks_like_jwe("eyJshort"))
        self.assertFalse(_looks_like_jwe("not-a-jwe-" + ("x" * 200)))
        self.assertFalse(_looks_like_jwe(""))
        self.assertFalse(_looks_like_jwe(None))

    def test_extract_token_from_url(self):
        url = (
            "https://www.mercedes-benz.co.uk/passengercars/my-area/my-mercedes-benz.html"
            f"?b2xProvider=CIAS&b2xFlow=LOGIN&token={_FAKE_JWE}"
        )
        self.assertEqual(_extract_token_from_url(url), _FAKE_JWE)

        # Short / non-JWE token in URL must be ignored (the old bug)
        bad = "https://www.mercedes-benz.co.uk/?token=abc123"
        self.assertIsNone(_extract_token_from_url(bad))

        # Non-mercedes domain ignored
        ext = f"https://example.com/?token={_FAKE_JWE}"
        self.assertIsNone(_extract_token_from_url(ext))

        # No token param
        self.assertIsNone(_extract_token_from_url("https://www.mercedes-benz.co.uk/"))
        self.assertIsNone(_extract_token_from_url(""))


# A UUID-shaped intermediate token (the ?token=<uuid> from the CIAS callback
# redirect) — must NEVER be accepted as a bearer JWE.
_FAKE_UUID = "00000000-0000-4000-8000-000000000000"


class TestTokenStorage(unittest.TestCase):
    """Tests for get_stored_token / set_stored_token JWE validation."""

    @patch("my_car_cli.auth.os.getenv", return_value=None)
    @patch("my_car_cli.auth.keyring")
    @patch("my_car_cli.auth.TOKEN_FILE")
    def test_get_stored_token_prefers_valid_keyring(self, mock_tf, mock_kr, _):
        mock_kr.get_password.return_value = _FAKE_JWE
        mock_tf.exists.return_value = True
        token = get_stored_token()
        self.assertEqual(token, _FAKE_JWE)
        mock_tf.read_text.assert_not_called()

    @patch("my_car_cli.auth.os.getenv", return_value=None)
    @patch("my_car_cli.auth.keyring")
    @patch("my_car_cli.auth.TOKEN_FILE")
    def test_get_stored_token_falls_through_stale_keyring(self, mock_tf, mock_kr, _):
        """Stale UUID in keyring → fall through to token file with valid JWE."""
        mock_kr.get_password.return_value = _FAKE_UUID
        mock_tf.exists.return_value = True
        mock_tf.read_text.return_value = _FAKE_JWE
        token = get_stored_token()
        self.assertEqual(token, _FAKE_JWE)
        mock_kr.delete_password.assert_called_once_with(SERVICE_NAME, KEYRING_USER)

    @patch("my_car_cli.auth.os.getenv", return_value=None)
    @patch("my_car_cli.auth.keyring")
    @patch("my_car_cli.auth.TOKEN_FILE")
    def test_get_stored_token_returns_none_when_all_invalid(self, mock_tf, mock_kr, _):
        mock_kr.get_password.return_value = _FAKE_UUID
        mock_tf.exists.return_value = True
        mock_tf.read_text.return_value = "also-not-a-jwe"
        token = get_stored_token()
        self.assertIsNone(token)

    @patch("my_car_cli.auth.os.getenv", return_value=_FAKE_JWE)
    @patch("my_car_cli.auth.keyring")
    def test_get_stored_token_env_var_valid_jwe(self, mock_kr, _):
        token = get_stored_token()
        self.assertEqual(token, _FAKE_JWE)
        mock_kr.get_password.assert_not_called()

    @patch("my_car_cli.auth.os.getenv", return_value=_FAKE_UUID)
    @patch("my_car_cli.auth.keyring")
    @patch("my_car_cli.auth.TOKEN_FILE")
    def test_get_stored_token_skips_non_jwe_env_var(self, mock_tf, mock_kr, _):
        """Non-JWE env var is skipped; falls through to keyring."""
        mock_kr.get_password.return_value = _FAKE_JWE
        mock_tf.exists.return_value = False
        token = get_stored_token()
        self.assertEqual(token, _FAKE_JWE)

    @patch("my_car_cli.auth.keyring")
    def test_set_stored_token_rejects_non_jwe(self, mock_kr):
        result = set_stored_token(_FAKE_UUID)
        self.assertFalse(result)
        mock_kr.set_password.assert_not_called()

    @patch("my_car_cli.auth.keyring")
    def test_set_stored_token_accepts_jwe(self, mock_kr):
        result = set_stored_token(_FAKE_JWE)
        self.assertTrue(result)
        mock_kr.set_password.assert_called_once_with(SERVICE_NAME, KEYRING_USER, _FAKE_JWE)


if __name__ == "__main__":
    unittest.main()
