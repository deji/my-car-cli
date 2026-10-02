import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from typer.testing import CliRunner
from my_car_cli.auth import (
    _looks_like_jwe,
    _extract_token_from_url,
    _try_exchange_callback_uuid,
    extract_intermediate_uuid,
    get_stored_token,
    set_stored_token,
)
from my_car_cli.api import CarAPIError, TokenExpiredException
from my_car_cli.config import (
    load_config,
    update_config_key,
    set_config_dir,
    reset_config_dir,
    validate_pressure_unit,
    validate_ttl,
)
from my_car_cli.display import format_timestamp, km_to_miles, kpa_to_psi, render_dashboard
from my_car_cli.main import app

# A realistic-shaping CIAS JWE: starts with "eyJ" and is well over 100 chars.
_FAKE_JWE = "eyJlbmMiOiJBMjU2Q0JDLUhTNTEyIiwiYWxnIjoiZGlyIn0.." + ("A" * 400)


_MODULE_TEMP_DIR = None


def setUpModule():
    global _MODULE_TEMP_DIR
    _MODULE_TEMP_DIR = tempfile.TemporaryDirectory()
    set_config_dir(Path(_MODULE_TEMP_DIR.name))


def tearDownModule():
    global _MODULE_TEMP_DIR
    reset_config_dir()
    if _MODULE_TEMP_DIR is not None:
        _MODULE_TEMP_DIR.cleanup()


class TestMyCarCLI(unittest.TestCase):

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        set_config_dir(Path(self._temp_dir.name))

    def tearDown(self):
        reset_config_dir()
        self._temp_dir.cleanup()

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

    def test_extract_intermediate_uuid(self):
        uuid_val = "00000000-0000-4000-8000-000000000000"

        # Mercedes host with valid UUID accepted
        url_valid = (
            "https://www.mercedes-benz.co.uk/passengercars/my-area/my-mercedes-benz.html"
            f"?b2xProvider=CIAS&b2xFlow=LOGIN&token={uuid_val}"
        )
        self.assertEqual(extract_intermediate_uuid(url_valid), uuid_val)

        # Uppercase UUID accepted and returned lowercased (canonical)
        url_upper = "https://www.mercedes-benz.co.uk/?token=12345678-ABCD-EF01-2345-6789ABCDEF01"
        self.assertEqual(extract_intermediate_uuid(url_upper), "12345678-abcd-ef01-2345-6789abcdef01")

        # JWE rejected
        url_jwe = f"https://www.mercedes-benz.co.uk/?token={_FAKE_JWE}"
        self.assertIsNone(extract_intermediate_uuid(url_jwe))

        # Other host rejected
        url_other = f"https://example.com/?token={uuid_val}"
        self.assertIsNone(extract_intermediate_uuid(url_other))

        # Other host attempting to spoof via query or path rejected
        url_spoof1 = f"https://example.com/mercedes-benz.co.uk?token={uuid_val}"
        self.assertIsNone(extract_intermediate_uuid(url_spoof1))
        url_spoof2 = f"https://not-mercedes-benz.co.uk/?token={uuid_val}"
        self.assertIsNone(extract_intermediate_uuid(url_spoof2))

        # Empty URL, missing params, non-matching token rejected
        self.assertIsNone(extract_intermediate_uuid(""))
        self.assertIsNone(extract_intermediate_uuid(None))
        self.assertIsNone(extract_intermediate_uuid("https://www.mercedes-benz.co.uk/"))
        self.assertIsNone(extract_intermediate_uuid("https://www.mercedes-benz.co.uk/?token=not-a-uuid"))

    def test_config_validation_and_updates(self):
        # 1. Validation helpers
        self.assertEqual(validate_pressure_unit("psi"), "PSI")
        self.assertEqual(validate_pressure_unit("PSI"), "PSI")
        self.assertEqual(validate_pressure_unit("kpa"), "KPA")
        self.assertEqual(validate_pressure_unit("KPA"), "KPA")

        with self.assertRaises(ValueError):
            validate_pressure_unit("BAR")
        with self.assertRaises(ValueError):
            validate_pressure_unit("ATM")
        with self.assertRaises(ValueError):
            validate_pressure_unit("")

        self.assertEqual(validate_ttl(1), 1)
        self.assertEqual(validate_ttl(15), 15)

        with self.assertRaises(ValueError):
            validate_ttl(0)
        with self.assertRaises(ValueError):
            validate_ttl(-5)

        # 2. Typer CLI runner execution
        runner = CliRunner()

        # Reject bad unit
        res_bad_unit = runner.invoke(app, ["config", "--unit", "BAR"])
        self.assertEqual(res_bad_unit.exit_code, 1)
        self.assertIn("Error", res_bad_unit.output)

        # Reject non-positive TTL
        res_zero_ttl = runner.invoke(app, ["config", "--ttl", "0"])
        self.assertEqual(res_zero_ttl.exit_code, 1)
        self.assertIn("Error", res_zero_ttl.output)

        res_neg_ttl = runner.invoke(app, ["config", "--ttl", "-5"])
        self.assertEqual(res_neg_ttl.exit_code, 1)
        self.assertIn("Error", res_neg_ttl.output)

        # Ensure config on disk was not corrupted by failed invocations
        cfg = load_config()
        self.assertNotEqual(cfg.get("pressure_unit"), "BAR")
        self.assertNotEqual(cfg.get("cache_ttl_minutes"), 0)

        # Valid unit update (case-insensitive -> stored uppercased)
        res_valid_unit = runner.invoke(app, ["config", "--unit", "kpa"])
        self.assertEqual(res_valid_unit.exit_code, 0)
        self.assertEqual(load_config().get("pressure_unit"), "KPA")

        # Valid TTL update
        res_valid_ttl = runner.invoke(app, ["config", "--ttl", "45"])
        self.assertEqual(res_valid_ttl.exit_code, 0)
        self.assertEqual(load_config().get("cache_ttl_minutes"), 45)

        # --show prints current config
        res_show = runner.invoke(app, ["config", "--show"])
        self.assertEqual(res_show.exit_code, 0)
        self.assertIn("Pressure Unit: KPA", res_show.output)
        self.assertIn("Cache TTL: 45 minutes", res_show.output)

    def test_render_dashboard_with_days(self):
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
                "tires": [],
                "brakeFluid": {"fluidLevelWarning": False},
            },
        }

        mock_next_service = {
            "days": {"remaining": 40},
            "mileage": {"remaining": 5000, "status": "OK", "limit": 60000, "unit": "KM"},
            "extent": "UNKNOWN",
            "status": {"value": "OK", "references": ["MILEAGE"]},
        }

        try:
            render_dashboard(mock_status, mock_next_service, is_cached=True)
        except Exception as e:
            self.fail(f"render_dashboard raised an exception: {e}")


# A UUID-shaped intermediate token (the ?token=<uuid> from the CIAS callback
# redirect) — must NEVER be accepted as a bearer JWE.
_FAKE_UUID = "00000000-0000-4000-8000-000000000000"


class TestTokenStorage(unittest.TestCase):
    """Tests for get_stored_token / set_stored_token JWE validation."""

    @patch("my_car_cli.auth.os.getenv", return_value=None)
    @patch("my_car_cli.auth.TOKEN_FILE")
    def test_get_stored_token_reads_file(self, mock_tf, _):
        mock_tf.exists.return_value = True
        mock_tf.read_text.return_value = _FAKE_JWE
        self.assertEqual(get_stored_token(), _FAKE_JWE)

    @patch("my_car_cli.auth.os.getenv", return_value=None)
    @patch("my_car_cli.auth.TOKEN_FILE")
    def test_get_stored_token_returns_none_when_file_invalid(self, mock_tf, _):
        mock_tf.exists.return_value = True
        mock_tf.read_text.return_value = "also-not-a-jwe"
        self.assertIsNone(get_stored_token())

    @patch("my_car_cli.auth.os.getenv", return_value=_FAKE_JWE)
    @patch("my_car_cli.auth.TOKEN_FILE")
    def test_get_stored_token_env_var_valid_jwe(self, mock_tf, _):
        token = get_stored_token()
        self.assertEqual(token, _FAKE_JWE)
        mock_tf.exists.assert_not_called()

    @patch("my_car_cli.auth.os.getenv", return_value=_FAKE_UUID)
    @patch("my_car_cli.auth.TOKEN_FILE")
    def test_get_stored_token_skips_non_jwe_env_var(self, mock_tf, _):
        """Non-JWE env var is skipped; falls through to the token file."""
        mock_tf.exists.return_value = True
        mock_tf.read_text.return_value = _FAKE_JWE
        self.assertEqual(get_stored_token(), _FAKE_JWE)

    @patch("my_car_cli.auth.TOKEN_FILE")
    @patch("my_car_cli.auth.CONFIG_DIR")
    def test_set_stored_token_rejects_non_jwe(self, _dir, mock_tf):
        result = set_stored_token(_FAKE_UUID)
        self.assertFalse(result)
        mock_tf.write_text.assert_not_called()

    @patch("my_car_cli.auth.TOKEN_FILE")
    @patch("my_car_cli.auth.CONFIG_DIR")
    def test_set_stored_token_writes_file(self, _dir, mock_tf):
        result = set_stored_token(_FAKE_JWE)
        self.assertTrue(result)
        mock_tf.write_text.assert_called_once_with(_FAKE_JWE, encoding="utf-8")


_NEW_JWE = "eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2Q0JDLUhTNTEyIn0.." + ("B" * 400)


class TestStatusRefreshWiring(unittest.TestCase):
    """
    401 → refresh_token() once → set_stored_token() → fetch retried once.
    All collaborators are mocked; no socket is opened.
    """

    _VIN = "TESTVIN0000000000"

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        set_config_dir(Path(self._temp_dir.name))
        update_config_key("vin", self._VIN)
        self._runner = CliRunner()

    def tearDown(self):
        reset_config_dir()
        self._temp_dir.cleanup()

    def _invoke_status(
        self,
        fetch_side_effect,
        refresh_side_effect=None,
        args=None,
        set_stored_token_result=True,
    ):
        with (
            patch("my_car_cli.main.get_stored_token", return_value=_FAKE_JWE),
            patch("my_car_cli.main.get_cached_data", return_value=None),
            patch("my_car_cli.main.save_cached_data") as mock_save,
            patch("my_car_cli.main.render_dashboard") as mock_render,
            patch("my_car_cli.main.fetch_vehicle_status") as mock_fetch,
            patch("my_car_cli.main.refresh_token") as mock_refresh,
            patch("my_car_cli.main.set_stored_token") as mock_set,
        ):
            mock_fetch.side_effect = fetch_side_effect
            mock_set.return_value = set_stored_token_result
            if refresh_side_effect is not None:
                mock_refresh.side_effect = refresh_side_effect
            else:
                mock_refresh.return_value = _NEW_JWE
            result = self._runner.invoke(app, args or ["status"])
            return result, mock_fetch, mock_refresh, mock_set, mock_save, mock_render

    def test_401_refresh_recovers_and_retries_once(self):
        result, mock_fetch, mock_refresh, mock_set, mock_save, mock_render = self._invoke_status(
            [TokenExpiredException("expired"), ({"liveData": {}}, {})]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Session renewed", result.output)
        mock_refresh.assert_called_once_with(_FAKE_JWE)
        mock_set.assert_called_once_with(_NEW_JWE)
        self.assertEqual(mock_fetch.call_count, 2)
        self.assertEqual(mock_fetch.call_args_list[0].args, (_FAKE_JWE, self._VIN))
        self.assertEqual(mock_fetch.call_args_list[1].args, (_NEW_JWE, self._VIN))
        mock_save.assert_called_once()
        mock_render.assert_called_once()

    def test_refresh_failure_shows_session_expired(self):
        result, mock_fetch, mock_refresh, mock_set, mock_save, _ = self._invoke_status(
            TokenExpiredException("expired"),
            refresh_side_effect=TokenExpiredException("refresh window closed"),
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("Session Expired", result.output)
        mock_refresh.assert_called_once_with(_FAKE_JWE)
        mock_set.assert_not_called()
        mock_fetch.assert_called_once()
        mock_save.assert_not_called()

    def test_retry_failure_shows_session_expired(self):
        result, mock_fetch, mock_refresh, mock_set, _, _ = self._invoke_status(
            [TokenExpiredException("expired"), TokenExpiredException("still expired")]
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("Session Expired", result.output)
        mock_refresh.assert_called_once_with(_FAKE_JWE)
        mock_set.assert_called_once_with(_NEW_JWE)
        self.assertEqual(mock_fetch.call_count, 2)

    def test_set_stored_token_failure_shows_session_expired_without_retry(self):
        result, mock_fetch, mock_refresh, mock_set, mock_save, _ = self._invoke_status(
            TokenExpiredException("expired"),
            set_stored_token_result=False,
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("Session Expired", result.output)
        self.assertNotIn("Session renewed", result.output)
        mock_refresh.assert_called_once_with(_FAKE_JWE)
        mock_set.assert_called_once_with(_NEW_JWE)
        mock_fetch.assert_called_once()
        mock_save.assert_not_called()

    def test_refresh_api_error_shows_api_error_panel(self):
        result, mock_fetch, mock_refresh, mock_set, _, _ = self._invoke_status(
            TokenExpiredException("expired"),
            refresh_side_effect=CarAPIError("Token refresh API error (500)."),
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("Vehicle API Error", result.output)
        mock_set.assert_not_called()
        mock_fetch.assert_called_once()

    def test_status_refresh_flag_still_bypasses_cache(self):
        """--refresh means cache bypass only; it must not force a token refresh."""
        with (
            patch("my_car_cli.main.get_stored_token", return_value=_FAKE_JWE),
            patch("my_car_cli.main.get_cached_data") as mock_cached,
            patch("my_car_cli.main.save_cached_data"),
            patch("my_car_cli.main.render_dashboard"),
            patch(
                "my_car_cli.main.fetch_vehicle_status",
                return_value=({"liveData": {}}, {}),
            ) as mock_fetch,
            patch("my_car_cli.main.refresh_token") as mock_refresh,
            patch("my_car_cli.main.set_stored_token"),
        ):
            result = self._runner.invoke(app, ["status", "--refresh"])

        self.assertEqual(result.exit_code, 0)
        mock_cached.assert_not_called()
        mock_fetch.assert_called_once_with(_FAKE_JWE, self._VIN)
        mock_refresh.assert_not_called()


class TestCallbackUuidExchange(unittest.TestCase):
    """The Playwright wait loop must exchange each callback UUID at most once."""

    _CALLBACK_URL = (
        "https://www.mercedes-benz.co.uk/passengercars/my-area/my-mercedes-benz.html"
        f"?b2xProvider=CIAS&b2xFlow=LOGIN&token={_FAKE_UUID}"
    )
    _CALLBACK_URL_2 = "https://www.mercedes-benz.co.uk/?token=11111111-2222-4333-8444-555555555555"

    @patch("my_car_cli.auth.exchange_intermediate_token")
    def test_exchange_success_returns_jwe_and_marks_attempted(self, mock_exchange):
        mock_exchange.return_value = _FAKE_JWE
        attempted: set = set()

        self.assertEqual(_try_exchange_callback_uuid(self._CALLBACK_URL, attempted), _FAKE_JWE)
        self.assertIn(_FAKE_UUID, attempted)

        # Same UUID again → no second call and no token.
        self.assertIsNone(_try_exchange_callback_uuid(self._CALLBACK_URL, attempted))
        mock_exchange.assert_called_once_with(_FAKE_UUID)

    @patch("my_car_cli.auth.console")
    @patch("my_car_cli.auth.exchange_intermediate_token")
    def test_exchange_failure_is_not_retried(self, mock_exchange, mock_console):
        mock_exchange.side_effect = CarAPIError("boom")
        attempted: set = set()

        self.assertIsNone(_try_exchange_callback_uuid(self._CALLBACK_URL, attempted))
        self.assertIsNone(_try_exchange_callback_uuid(self._CALLBACK_URL, attempted))
        mock_exchange.assert_called_once_with(_FAKE_UUID)
        # A one-line dim note is printed; the wait loop is not aborted.
        mock_console.print.assert_called_once()
        self.assertIn("boom", mock_console.print.call_args.args[0])

    @patch("my_car_cli.auth.exchange_intermediate_token")
    def test_non_uuid_url_never_exchanges(self, mock_exchange):
        attempted: set = set()
        for url in (
            "https://www.mercedes-benz.co.uk/",
            f"https://www.mercedes-benz.co.uk/?token={_FAKE_JWE}",
            "chrome-error://chromewebdata/",
            "",
        ):
            self.assertIsNone(_try_exchange_callback_uuid(url, attempted))
        mock_exchange.assert_not_called()

    @patch("my_car_cli.auth.exchange_intermediate_token")
    def test_distinct_uuids_each_exchanged_once(self, mock_exchange):
        mock_exchange.side_effect = [_FAKE_JWE, _NEW_JWE]
        attempted: set = set()

        self.assertEqual(_try_exchange_callback_uuid(self._CALLBACK_URL, attempted), _FAKE_JWE)
        self.assertEqual(_try_exchange_callback_uuid(self._CALLBACK_URL_2, attempted), _NEW_JWE)
        self.assertIsNone(_try_exchange_callback_uuid(self._CALLBACK_URL, attempted))
        self.assertEqual(mock_exchange.call_count, 2)
        mock_exchange.assert_any_call(_FAKE_UUID)
        mock_exchange.assert_any_call("11111111-2222-4333-8444-555555555555")


if __name__ == "__main__":
    unittest.main()
