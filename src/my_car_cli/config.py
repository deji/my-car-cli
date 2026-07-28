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
