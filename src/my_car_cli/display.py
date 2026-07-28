import sys
from datetime import datetime
from typing import Any, Dict, Optional
from rich.console import Console
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text
from my_car_cli.config import load_config

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console()

KPA_TO_PSI = 0.145038
KM_TO_MILES = 0.621371


def format_timestamp(timestamp_ms: Optional[int]) -> str:
    """Format Unix timestamp in ms to human readable string."""
    if not timestamp_ms:
        return "Unknown"
    dt = datetime.fromtimestamp(timestamp_ms / 1000.0)
    return dt.strftime("%d %b %Y at %H:%M")


def kpa_to_psi(kpa: float) -> float:
    """Convert pressure from KPA to PSI."""
    return round(kpa * KPA_TO_PSI, 1)


def km_to_miles(km: float) -> int:
    """Convert distance from KM to miles."""
    return round(km * KM_TO_MILES)


def render_dashboard(
    status_data: Dict[str, Any],
    next_service_data: Optional[Dict[str, Any]] = None,
    is_cached: bool = False,
    cached_at: Optional[float] = None,
) -> None:
    """Render vehicle status dashboard using Rich."""
    config = load_config()
    pressure_unit = config.get("pressure_unit", "PSI").upper()
    
    live_data = status_data.get("liveData", {})
    last_update_ts = live_data.get("lastUpdateTimestamp")
    
    # 1. Header
    title = Text("🚗  My Car  —  Status", style="bold white on blue", justify="center")
    header_panel = Panel(title, border_style="cyan", padding=(0, 2))
    console.print()
    console.print(header_panel)

    # 2. Battery Section
    levels = live_data.get("levels", [])
    soc_val = None
    for level in levels:
        if level.get("type") in ("ELECTRIC", "HYBRID"):
            soc_val = level.get("value")
            break
    if soc_val is None and levels:
        soc_val = levels[0].get("value")

    ranges = live_data.get("ranges", [])
    range_val = None
    range_unit = "miles"
    for r in ranges:
        if r.get("type") in ("ELECTRIC", "HYBRID"):
            range_val = r.get("value")
            range_unit = r.get("unit", "miles").lower()
            break
    if range_val is None and ranges:
        range_val = ranges[0].get("value")

    console.print("\n  [bold cyan]🔋  Battery[/bold cyan]")
    if soc_val is not None:
        soc_percent = max(0, min(100, int(soc_val)))
        
        # Build progress bar string
        bar_width = 20
        filled = int((soc_percent / 100) * bar_width)
        unfilled = bar_width - filled
        
        color = "green" if soc_percent > 30 else ("yellow" if soc_percent > 15 else "red")
        bar_str = f"[{color}]{'█' * filled}[/{color}][dim]{'░' * unfilled}[/dim]"
        
        console.print(f"  {bar_str}  [bold white]{soc_percent}%[/bold white]")
    if range_val is not None:
        console.print(f"  [dim]Remaining range:[/dim] [bold green]{range_val} {range_unit}[/bold green]")

    # 3. Odometer Section
    mileage_dict = live_data.get("mileage", {})
    mileage_val = mileage_dict.get("value")
    mileage_unit = mileage_dict.get("unit", "MILES").lower()
    
    console.print("\n  [bold cyan]🛣️   Odometer[/bold cyan]")
    if mileage_val is not None:
        console.print(f"  [bold white]{mileage_val:,}[/bold white] {mileage_unit}")

    # 4. Tyre Pressures Section
    tires = live_data.get("tires", [])
    if tires:
        console.print("\n  [bold cyan]⭕  Tyre Pressures[/bold cyan]")
        table = Table(show_header=True, header_style="bold magenta", border_style="dim", box=None)
        table.add_column("Position", style="white", width=14)
        if pressure_unit == "KPA":
            table.add_column("KPA", justify="right", style="cyan", width=8)
        else:
            table.add_column("PSI", justify="right", style="cyan", width=8)
        table.add_column("Status", justify="center", width=10)

        pos_labels = {
            "FRONT_LEFT": "Front Left",
            "FRONT_RIGHT": "Front Right",
            "REAR_LEFT": "Rear Left",
            "REAR_RIGHT": "Rear Right",
        }

        for tire in tires:
            t_type = tire.get("type", "")
            t_kpa = tire.get("value", 0.0)
            t_warn = tire.get("warning", "OK")
            
            pos_name = pos_labels.get(t_type, t_type.replace("_", " ").title())
            
            if pressure_unit == "KPA":
                val_str = f"{t_kpa:.1f}"
            else:
                psi_val = kpa_to_psi(t_kpa)
                val_str = f"{psi_val:.1f}"
            
            if t_warn.upper() == "OK":
                status_str = "[bold green]✅ OK[/bold green]"
            else:
                status_str = f"[bold red]⚠️ {t_warn}[/bold red]"
                
            table.add_row(pos_name, val_str, status_str)

        console.print(table)

    # 5. Next Service Section
    if next_service_data:
        ns_mileage = next_service_data.get("mileage", {})
        remaining = ns_mileage.get("remaining")
        ns_status = ns_mileage.get("status", "OK")
        ns_unit = ns_mileage.get("unit", "KM").upper()
        
        console.print("\n  [bold cyan]🔧  Next Service[/bold cyan]")
        if remaining is not None:
            if ns_unit == "KM":
                remaining_km = remaining
                remaining_m = km_to_miles(remaining)
            else:
                remaining_m = remaining
                remaining_km = round(remaining / KM_TO_MILES)
                
            console.print(
                f"  Due in ~[bold white]{remaining_m:,}[/bold white] miles ({remaining_km:,} km)   "
                f"Status: [bold green]{ns_status}[/bold green]"
            )

    # 6. Footer
    vehicle_time = format_timestamp(last_update_ts)
    console.print(f"\n  [dim]Vehicle data from:[/dim] {vehicle_time}")

    if is_cached and cached_at is not None:
        cache_time = format_timestamp(int(cached_at * 1000))
        console.print(f"  [dim]Cached at:[/dim]          {cache_time} [yellow](cached)[/yellow]")
    elif not is_cached:
        fetch_time = format_timestamp(int(datetime.now().timestamp() * 1000))
        console.print(f"  [dim]Fetched at:[/dim]         {fetch_time} [green](live)[/green]")
    console.print()
