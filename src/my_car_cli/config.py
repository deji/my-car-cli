import json
from pathlib import Path
from typing import Any, Dict

CONFIG_DIR = Path.home() / ".my-car-cli"
CONFIG_FILE = CONFIG_DIR / "config.json"
CACHE_FILE = CONFIG_DIR / "cache.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "vin": None,
    "locale": "en-GB",
    "pressure_unit": "PSI",
    "cache_ttl_minutes": 15,
}

VALID_PRESSURE_UNITS = ("PSI", "KPA")


def validate_pressure_unit(unit: str) -> str:
    """Validate tyre pressure unit (must be PSI or KPA, case-insensitive). Returns uppercased unit."""
    if not isinstance(unit, str) or unit.upper() not in VALID_PRESSURE_UNITS:
        raise ValueError(f"Invalid tyre pressure unit '{unit}'. Must be PSI or KPA.")
    return unit.upper()


def validate_ttl(ttl: int) -> int:
    """Validate cache TTL minutes (must be >= 1)."""
    if not isinstance(ttl, int) or ttl < 1:
        raise ValueError(f"Cache TTL must be at least 1 minute (got {ttl}).")
    return ttl


def set_config_dir(path: Path) -> None:
    """Set custom config directory (e.g. for testing)."""
    global CONFIG_DIR, CONFIG_FILE, CACHE_FILE
    CONFIG_DIR = Path(path)
    CONFIG_FILE = CONFIG_DIR / "config.json"
    CACHE_FILE = CONFIG_DIR / "cache.json"


def reset_config_dir() -> None:
    """Reset config directory to default ~/.my-car-cli."""
    global CONFIG_DIR, CONFIG_FILE, CACHE_FILE
    CONFIG_DIR = Path.home() / ".my-car-cli"
    CONFIG_FILE = CONFIG_DIR / "config.json"
    CACHE_FILE = CONFIG_DIR / "cache.json"


def ensure_config_dir() -> Path:
    """Ensure that the configuration directory exists."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return CONFIG_DIR


def load_config() -> Dict[str, Any]:
    """Load configuration from ~/.my-car-cli/config.json, creating defaults if absent."""
    ensure_config_dir()
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            config = DEFAULT_CONFIG.copy()
            config.update(data)
            return config
    except Exception:
        return DEFAULT_CONFIG.copy()


def save_config(config: Dict[str, Any]) -> None:
    """Save configuration dictionary to disk."""
    ensure_config_dir()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def update_config_key(key: str, value: Any) -> Dict[str, Any]:
    """Update a single configuration key."""
    config = load_config()
    config[key] = value
    save_config(config)
    return config
