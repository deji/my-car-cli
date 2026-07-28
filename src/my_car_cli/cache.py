import json
import time
from pathlib import Path
from typing import Any, Dict, Optional
from my_car_cli.config import CACHE_FILE, ensure_config_dir, load_config


def get_cached_data(vin: str) -> Optional[Dict[str, Any]]:
    """Return cached vehicle data if valid and within TTL."""
    if not CACHE_FILE.exists():
        return None

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)

        cached_at = cache.get("cached_at", 0)
        cached_vin = cache.get("vin")

        if cached_vin != vin:
            return None

        config = load_config()
        ttl_seconds = config.get("cache_ttl_minutes", 15) * 60

        if time.time() - cached_at > ttl_seconds:
            return None

        return cache.get("data")
    except Exception:
        return None


def get_cache_timestamp() -> Optional[float]:
    """Return the cached_at timestamp from the cache file, or None."""
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        return cache.get("cached_at")
    except Exception:
        return None


def save_cached_data(status_data: Dict[str, Any], next_service_data: Dict[str, Any], vin: str) -> None:
    """Save response data to disk cache."""
    ensure_config_dir()
    cache_payload = {
        "cached_at": time.time(),
        "vin": vin,
        "data": {
            "status_data": status_data,
            "next_service_data": next_service_data,
        },
    }
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache_payload, f, indent=2)


def clear_cache() -> None:
    """Clear local response cache."""
    if CACHE_FILE.exists():
        try:
            CACHE_FILE.unlink()
        except Exception:
            pass
