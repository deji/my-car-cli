import os
import sys
import time
import keyring
from pathlib import Path
from typing import Optional
from rich.console import Console

SERVICE_NAME = "my-car-cli"
KEYRING_USER = "bearer_token"
CONFIG_DIR = Path.home() / ".my-car-cli"
BROWSER_PROFILE_DIR = CONFIG_DIR / "browser-profile"
TOKEN_FILE = CONFIG_DIR / "token"
console = Console()

# Browser fingerprint constants
# Let Playwright provide the default Chromium UA — it matches the actual browser
# engine. Previously spoofed Firefox 153, but that caused the site's JS to serve
# Firefox-specific code paths that hung in Chromium.
LANDING_URL = "https://www.mercedes-benz.co.uk/"
CIAS_AUTH_URL = (
    "https://api.oneweb.mercedes-benz.com/cias/v1/authentication"
    "?prompt=DEFAULT"
    "&locale=en_GB"
    "&tenantId=oneweb"
    "&redirectUrl=https%3A%2F%2Fwww.mercedes-benz.co.uk%2Fpassengercars%2Fmy-area%2Fmy-mercedes-benz.html%3Fb2xProvider%3DCIAS%26b2xFlow%3DLOGIN"
    "&forceMfa=false"
)
LOGIN_TIMEOUT_SECONDS = 300
PAGE_LOAD_TIMEOUT_MS = 30000


def _looks_like_jwe(token: str) -> bool:
    """CIAS-issued JWE tokens are Base64URL, start with 'eyJ', and are long."""
    return bool(token) and token.startswith("eyJ") and len(token) > 100


def _extract_token_from_url(url: str) -> Optional[str]:
    """Pull a JWE token from a ?token= query param on a mercedes-benz.co.uk URL."""
    if not url or "token=" not in url or "mercedes-benz.co.uk" not in url:
        return None
    from urllib.parse import urlparse, parse_qs

    params = parse_qs(urlparse(url).query)
    token_val = params.get("token", [None])[0]
    if token_val and _looks_like_jwe(token_val):
        return token_val
    return None


def get_stored_token() -> Optional[str]:
    env_token = os.getenv("MY_CAR_AUTH_TOKEN")
    if env_token:
        cleaned = env_token.strip().removeprefix("Bearer ").strip()
        if _looks_like_jwe(cleaned):
            return cleaned

    try:
        token = keyring.get_password(SERVICE_NAME, KEYRING_USER)
        if token:
            cleaned = token.strip().removeprefix("Bearer ").strip()
            if _looks_like_jwe(cleaned):
                return cleaned
            # Stale/non-JWE value in keyring (e.g. a UUID from a pre-validation
            # login). Delete it so it doesn't shadow the token file every call.
            try:
                keyring.delete_password(SERVICE_NAME, KEYRING_USER)
            except Exception:
                pass
    except Exception:
        pass

    if TOKEN_FILE.exists():
        try:
            cleaned = TOKEN_FILE.read_text(encoding="utf-8").strip().removeprefix("Bearer ").strip()
            if _looks_like_jwe(cleaned):
                return cleaned
        except Exception:
            pass
    return None


def set_stored_token(token: str) -> bool:
    clean_token = token.strip().removeprefix("Bearer ").strip()
    if not _looks_like_jwe(clean_token):
        console.print("[bold red]Token does not look like a valid JWE (must start with 'eyJ' and be >100 chars). Not saving.[/bold red]")
        return False
    try:
        keyring.set_password(SERVICE_NAME, KEYRING_USER, clean_token)
    except Exception as e:
        console.print(f"[dim yellow]Keyring unavailable ({e}), falling back to file storage.[/dim yellow]")
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(clean_token, encoding="utf-8")
        console.print(f"[dim yellow]Token saved to {TOKEN_FILE}[/dim yellow]")
    return True


def delete_stored_token() -> None:
    try:
        keyring.delete_password(SERVICE_NAME, KEYRING_USER)
    except Exception:
        pass
    try:
        if TOKEN_FILE.exists():
            TOKEN_FILE.unlink()
    except Exception:
        pass


def is_playwright_available() -> bool:
    try:
        import playwright
        return True
    except ImportError:
        return False


def login_manual_paste() -> str:
    console.print("\n[bold cyan]My Car CLI Manual Authentication[/bold cyan]")
    console.print("[dim]------------------------------------------------------------[/dim]")
    console.print("Follow these steps to obtain your authentication token:")
    console.print("1. Open [bold yellow]https://id.mercedes-benz.com/ciam/auth/login[/bold yellow] in your browser and log in.")
    console.print("2. Open Browser Developer Tools ([bold white]F12[/bold white] or [bold white]Ctrl+Shift+I[/bold white]).")
    console.print("3. Go to the [bold white]Network[/bold white] tab and filter by [bold white]api.oneweb[/bold white].")
    console.print("4. Click on any request (e.g. status-information) and look under [bold white]Request Headers[/bold white].")
    console.print("5. Copy the value of the [bold white]Authorization[/bold white] header (starts with [dim]Bearer eyJ...[/dim]).\n")

    while True:
        raw_token = console.input("[bold green]Paste token here: [/bold green]").strip()
        if not raw_token:
            console.print("[bold red]Error: Token cannot be empty.[/bold red]")
            sys.exit(1)

        clean_token = raw_token.removeprefix("Bearer ").strip()
        if not _looks_like_jwe(clean_token):
            console.print("[bold red]That doesn't look like a JWE token (should start with 'eyJ' and be several hundred+ chars).[/bold red]")
            console.print("[dim]Make sure you copied the Authorization header from an api.oneweb.mercedes-benz.com request, not the URL bar token.[/dim]\n")
            continue

        set_stored_token(clean_token)
        console.print("[bold green]Token saved successfully to OS Credential Manager![/bold green]\n")
        return clean_token


def login_interactive_playwright() -> str:
    if not is_playwright_available():
        return login_manual_paste()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return login_manual_paste()

    console.print("\n[bold cyan]Launching browser for vehicle account login...[/bold cyan]")
    console.print("[dim]Browser profile and cookies are preserved between sessions.[/dim]")
    console.print("[dim]Log in to your vehicle account in the opened browser window.[/dim]\n")

    captured_token = None
    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    def handle_request(request):
        nonlocal captured_token
        if captured_token:
            return

        if "api.oneweb.mercedes-benz.com" in request.url:
            auth_header = request.headers.get("authorization") or request.headers.get(
                "Authorization"
            )
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header.removeprefix("Bearer ").strip()
                if _looks_like_jwe(token):
                    captured_token = token

    def handle_response(response):
        nonlocal captured_token
        if captured_token:
            return

        location = response.headers.get("location") or response.headers.get("Location")
        if location:
            token = _extract_token_from_url(location)
            if token:
                captured_token = token
                return

        if response.url:
            token = _extract_token_from_url(response.url)
            if token:
                captured_token = token

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 720},
            locale="en-GB",
            args=[
                "--disable-quic",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        # Hide automation signals that Akamai Bot Manager detects.
        # Playwright/Chromium sets navigator.webdriver=true and adds other
        # automation markers by default; this makes the page look like a
        # real browser session instead.
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-GB', 'en'] });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            window.chrome = window.chrome || { runtime: {} };
        """)

        page = context.new_page()

        console.print("[bold yellow]Step 1/2:[/bold yellow] Establishing browser session...")
        try:
            page.goto(LANDING_URL, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT_MS)
            page.wait_for_timeout(6000)
        except Exception as e:
            console.print(f"[dim]Landing page note (continuing): {e}[/dim]")

        # Attach token-capture listeners AFTER the landing page visit.
        # The landing page JS may replay a stale bearer token from a previous
        # session (persistent profile). Capturing it would shadow the fresh
        # token issued by the CIAS auth flow below.
        page.on("request", handle_request)
        page.on("response", handle_response)

        console.print("[bold yellow]Step 2/2:[/bold yellow] Starting CIAS authentication flow...")
        try:
            page.goto(CIAS_AUTH_URL, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT_MS)
        except Exception as e:
            console.print(f"[dim]Auth flow page note (continuing): {e}[/dim]")

        console.print("\n[bold yellow]Waiting for login and token capture...[/bold yellow]")
        console.print("[dim]Complete the login in the browser window. Token will be captured automatically.[/dim]")
        console.print("[dim]Minimize the console (do not close it) and use the browser window to log in.[/dim]\n")

        timeout_at = time.time() + LOGIN_TIMEOUT_SECONDS
        nav_error = False
        while not captured_token:
            page.wait_for_timeout(1000)
            if page.is_closed():
                break
            if time.time() > timeout_at:
                console.print("[bold yellow]Login timed out after 5 minutes.[/bold yellow]")
                break
            # Detect a Chromium navigation error page (e.g. ERR_HTTP2_PROTOCOL_ERROR,
            # ERR_QUIC_PROTOCOL_ERROR) so we don't hang silently for the full timeout.
            try:
                if page.url.startswith("chrome-error://"):
                    console.print("[bold red]Browser navigation error — page failed to load.[/bold red]")
                    console.print(f"[dim]Error page URL: {page.url}[/dim]")
                    console.print("[dim]Check the browser window for the error code. Falling back to manual token paste.[/dim]")
                    nav_error = True
                    break
            except Exception:
                pass

        context.close()

    if captured_token:
        set_stored_token(captured_token)
        console.print("[bold green]Token captured and saved successfully![/bold green]\n")
        return captured_token
    else:
        if not nav_error:
            console.print("[bold red]Could not capture token from browser session.[/bold red]")
        return login_manual_paste()
