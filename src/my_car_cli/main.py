import shutil
import sys
from typing import Optional
import typer
from rich.console import Console
from rich.panel import Panel

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from my_car_cli import __version__
from my_car_cli.api import CarAPIError, TokenExpiredException, fetch_vehicle_status, refresh_token
from my_car_cli.auth import (
    BROWSER_PROFILE_DIR,
    delete_stored_token,
    get_stored_token,
    is_playwright_available,
    login_interactive_playwright,
    login_manual_paste,
    set_stored_token,
)
from my_car_cli.cache import clear_cache, get_cache_timestamp, get_cached_data, save_cached_data
from my_car_cli.config import load_config, update_config_key, validate_pressure_unit, validate_ttl
from my_car_cli.display import render_dashboard

app = typer.Typer(
    name="my-car",
    help="🚗 A modern CLI for viewing car status.",
    add_completion=False,
)
console = Console()


def version_callback(value: bool):
    if value:
        console.print(f"[bold cyan]my-car-cli[/bold cyan] v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True, help="Show CLI version."
    ),
):
    pass


@app.command("login")
def login(
    manual: bool = typer.Option(
        False, "--manual", "-m", help="Force manual token paste mode."
    ),
    fresh: bool = typer.Option(
        False, "--fresh", "-f", help="Clear browser profile and start with a fresh fingerprint."
    ),
):
    """Automatically launch a browser, log in, and capture the auth token."""
    if fresh and BROWSER_PROFILE_DIR.exists():
        shutil.rmtree(BROWSER_PROFILE_DIR, ignore_errors=True)
        console.print("[dim]Browser profile cleared. Starting fresh.[/dim]")

    if manual or not is_playwright_available():
        if not is_playwright_available() and not manual:
            console.print("[dim]Playwright is not installed. Using manual token paste mode.[/dim]")
            console.print("[dim](Install automated login support with: uv add 'my-car-cli[login]')[/dim]")
        login_manual_paste()
    else:
        login_interactive_playwright()


@app.command("status")
def status(
    refresh: bool = typer.Option(
        False, "--refresh", "-r", help="Bypass cache and force a live API fetch."
    ),
    vin: Optional[str] = typer.Option(
        None, "--vin", help="Override VIN for this request."
    ),
):
    """View vehicle status (mileage, battery SoC, tyre pressures, next service)."""
    config = load_config()
    active_vin = vin if vin else config.get("vin")
    if not active_vin:
        console.print("[bold red]❌ No VIN specified. Please set one via config or --vin.[/bold red]")
        raise typer.Exit(code=1)

    token = get_stored_token()
    if not token:
        console.print("\n[bold red]❌ No authentication token found.[/bold red]")
        console.print("Please run [bold cyan]my-car login[/bold cyan] first to set your authentication token.\n")
        raise typer.Exit(code=1)

    # 1. Check cache if not refreshing
    if not refresh:
        cached = get_cached_data(active_vin)
        if cached:
            render_dashboard(
                status_data=cached.get("status_data", {}),
                next_service_data=cached.get("next_service_data"),
                is_cached=True,
                cached_at=get_cache_timestamp(),
            )
            return

    # 2. Fetch live data
    with console.status("[bold cyan]Fetching vehicle status...[/bold cyan]"):
        try:
            try:
                status_data, next_service_data = fetch_vehicle_status(token, active_vin)
            except TokenExpiredException:
                # The JWE can be rotated silently while the refresh window is
                # still open (#14/#17): refresh once, save the rotated token,
                # retry the fetch once. If refresh fails or the save is
                # rejected, re-raise into the "please re-login" path below.
                refreshed_token = refresh_token(token)
                if not set_stored_token(refreshed_token):
                    raise
                console.print("[green]✔ Session renewed — retrying...[/green]")
                status_data, next_service_data = fetch_vehicle_status(refreshed_token, active_vin)
        except TokenExpiredException:
            panel = Panel(
                "[bold red]Your authentication session has expired.[/bold red]\n\n"
                "Please run [bold cyan]my-car login[/bold cyan] to refresh your access token.",
                title="🔒 Session Expired",
                border_style="red",
            )
            console.print()
            console.print(panel)
            console.print()
            raise typer.Exit(code=1)
        except CarAPIError as e:
            panel = Panel(
                f"[bold red]API Request Failed:[/bold red]\n{e}",
                title="⚠️ Vehicle API Error",
                border_style="red",
            )
            console.print()
            console.print(panel)
            console.print()
            raise typer.Exit(code=1)
        except Exception as e:
            console.print(f"\n[bold red]An unexpected error occurred:[/bold red] {e}\n")
            raise typer.Exit(code=1)

    # 3. Save to cache & render
    save_cached_data(status_data, next_service_data, active_vin)
    render_dashboard(status_data=status_data, next_service_data=next_service_data, is_cached=False)


@app.command("logout")
def logout():
    """Clear stored authentication token and cached data."""
    delete_stored_token()
    clear_cache()
    console.print("[bold green]✔ Successfully logged out and cleared cache.[/bold green]")


@app.command("config")
def config_cmd(
    vin: Optional[str] = typer.Option(None, "--vin", help="Set vehicle VIN."),
    unit: Optional[str] = typer.Option(None, "--unit", help="Set tyre pressure unit (PSI, KPA)."),
    ttl: Optional[int] = typer.Option(None, "--ttl", help="Set cache TTL in minutes."),
    show: bool = typer.Option(False, "--show", help="Display current configuration."),
):
    """View or update configuration settings."""
    validated_unit = None
    if unit is not None:
        try:
            validated_unit = validate_pressure_unit(unit)
        except ValueError as e:
            console.print(f"[bold red]Error:[/bold red] {e}")
            raise typer.Exit(code=1)

    if ttl is not None:
        try:
            validate_ttl(ttl)
        except ValueError as e:
            console.print(f"[bold red]Error:[/bold red] {e}")
            raise typer.Exit(code=1)

    cfg = load_config()
    if vin:
        cfg = update_config_key("vin", vin)
        console.print(f"[bold green]✔ VIN set to:[/bold green] {vin}")
    if validated_unit is not None:
        cfg = update_config_key("pressure_unit", validated_unit)
        console.print(f"[bold green]✔ Tyre pressure unit set to:[/bold green] {validated_unit}")
    if ttl is not None:
        cfg = update_config_key("cache_ttl_minutes", ttl)
        console.print(f"[bold green]✔ Cache TTL set to:[/bold green] {ttl} minutes")

    if show or (not vin and not unit and ttl is None):
        console.print("\n[bold cyan]⚙️ My Car CLI Configuration[/bold cyan]")
        console.print(f"  [bold white]VIN:[/bold white] {cfg.get('vin')}")
        console.print(f"  [bold white]Locale:[/bold white] {cfg.get('locale')}")
        console.print(f"  [bold white]Pressure Unit:[/bold white] {cfg.get('pressure_unit')}")
        console.print(f"  [bold white]Cache TTL:[/bold white] {cfg.get('cache_ttl_minutes')} minutes\n")


if __name__ == "__main__":
    app()
