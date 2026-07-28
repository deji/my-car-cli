from typing import Any, Dict, Optional, Tuple
import httpx
from my_car_cli.config import load_config


class TokenExpiredException(Exception):
    """Exception raised when API returns 401 Unauthorized."""
    pass


class CarAPIError(Exception):
    """Exception raised for API request errors."""
    pass


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    "Origin": "https://www.mercedes-benz.co.uk",
    "Referer": "https://www.mercedes-benz.co.uk/",
}


def fetch_vehicle_status(token: str, vin: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Fetch status information and next service data from the vehicle API.
    Returns tuple of (status_data, next_service_data).
    """
    config = load_config()
    if not vin:
        vin = config.get("vin")
    locale = config.get("locale", "en-GB")

    status_url = f"https://api.oneweb.mercedes-benz.com/me/vsc/v1/user/vehicle/status-information?locale={locale}"
    next_service_url = "https://api.oneweb.mercedes-benz.com/domains/vehicles/maintenances/next-service"

    status_headers = {
        **DEFAULT_HEADERS,
        "Authorization": f"Bearer {token}",
        "X-Application-Name": "mmv-vehicle-stage",
        "X-me-FinOrVin": vin,
        "Accept": "*/*",
    }

    next_service_headers = {
        **DEFAULT_HEADERS,
        "Authorization": f"Bearer {token}",
        "x-application-name": "MMA",
        "x-language": "en",
        "x-market": "GB",
        "x-vehicle-id": vin,
        "Accept": "application/json;version=2",
    }

    with httpx.Client(timeout=10.0) as client:
        # 1. Fetch status information
        status_resp = client.get(status_url, headers=status_headers)
        if status_resp.status_code == 401:
            raise TokenExpiredException("Authentication token has expired.")
        elif status_resp.status_code != 200:
            raise CarAPIError(f"Status API error ({status_resp.status_code}): {status_resp.text}")

        try:
            status_data = status_resp.json()
        except Exception as e:
            raise CarAPIError(f"Failed to parse status response: {e}")

        # 2. Fetch next service data
        next_service_data = {}
        try:
            ns_resp = client.get(next_service_url, headers=next_service_headers)
            if ns_resp.status_code == 200:
                next_service_data = ns_resp.json()
            elif ns_resp.status_code == 401:
                raise TokenExpiredException("Authentication token has expired.")
        except TokenExpiredException:
            raise
        except Exception:
            # Service call is non-blocking for status information
            next_service_data = {}

        return status_data, next_service_data
