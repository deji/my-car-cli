import re
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

# Auth client base from the site's cias-login bundle (`ue.PROD`).
# bias-oidc/v1 is an older name in that bundle; live calls use cias/v1.
CIAS_AUTH_BASE_URL = "https://api.oneweb.mercedes-benz.com/cias/v1"
REQUEST_TIMEOUT_SECONDS = 10.0
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _looks_like_jwe(token: Any) -> bool:
    """
    CIAS-issued JWE tokens are Base64URL, start with 'eyJ', and are long.
    Local copy of auth._looks_like_jwe — duplicated to avoid an import cycle
    (auth imports api for these helpers).
    """
    return isinstance(token, str) and token.startswith("eyJ") and len(token) > 100


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


def _jwe_from_body(resp, error_label: str) -> str:
    """Accept either a raw JWE body or JSON {"jwe": "..."}."""
    text = resp.text if isinstance(getattr(resp, "text", None), str) else ""
    cleaned = text.strip().strip('"')
    if _looks_like_jwe(cleaned):
        return cleaned
    try:
        data = resp.json()
    except Exception:
        raise CarAPIError(f"{error_label} ({resp.status_code}): response was not JSON.")
    jwe = data.get("jwe") if isinstance(data, dict) else None
    if not _looks_like_jwe(jwe):
        raise CarAPIError(f"{error_label} ({resp.status_code}): response did not contain a valid JWE.")
    return jwe


def refresh_token(token: str) -> str:
    """
    Rotate the CIAS JWE via POST /cias/v1/jwe/refresh using the current
    (possibly stale) JWE as the credential. Returns the new JWE string.
    Does not save the token.
    """
    url = f"{CIAS_AUTH_BASE_URL}/jwe/refresh"
    headers = {
        **DEFAULT_HEADERS,
        "Authorization": f"Bearer {token}",
        "tenantId": "oneweb",
        "Accept": "application/json",
    }

    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        resp = client.post(url, headers=headers)

    if resp.status_code == 401:
        raise TokenExpiredException("Authentication token has expired.")
    if resp.status_code != 200:
        raise CarAPIError(f"Token refresh API error ({resp.status_code}).")

    return _jwe_from_body(resp, "Token refresh API error")


def exchange_intermediate_token(intermediate: str) -> str:
    """
    Exchange a CIAS callback intermediate token (canonical UUID) for a real
    JWE bearer via GET /cias/v1/jwe/{uuid}. The site reads this body as text.
    Returns the JWE string. Does not save the token.
    """
    if not isinstance(intermediate, str) or not _UUID_RE.match(intermediate):
        raise CarAPIError("Token exchange rejected: intermediate token is not a canonical UUID.")

    url = f"{CIAS_AUTH_BASE_URL}/jwe/{intermediate}"
    headers = {
        **DEFAULT_HEADERS,
        "Accept": "application/json",
        "tenantId": "oneweb",
    }

    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        resp = client.get(url, headers=headers)

    if resp.status_code == 401:
        raise TokenExpiredException("Authentication token has expired.")
    if resp.status_code != 200:
        raise CarAPIError(f"Token exchange API error ({resp.status_code}).")

    return _jwe_from_body(resp, "Token exchange API error")
