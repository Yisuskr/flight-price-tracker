"""
config.py - Loads settings from .env (secrets) and config.yaml (user preferences).
"""

import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")


def _require_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set. "
            f"Check your .env file."
        )
    return value


def load_config() -> dict:
    """
    Returns a merged config dict combining .env secrets and config.yaml settings.
    """
    config_path = ROOT / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(
            f"config.yaml not found at {config_path}. "
            "Copy .env.example to .env and fill in your settings."
        )

    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f)

    # Inject secrets from environment
    cfg["serpapi_key"] = _require_env("SERPAPI_KEY")
    cfg["smtp_user"] = _require_env("SMTP_USER")
    cfg["smtp_password"] = _require_env("SMTP_PASSWORD")
    # Optional additional source keys (sources silently disabled if not set)
    cfg["kiwi_api_key"] = os.getenv("KIWI_API_KEY", "")
    cfg["rapidapi_key"] = os.getenv("RAPIDAPI_KEY", "")
    cfg["aviasales_token"] = os.getenv("AVIASALES_TOKEN", "")
    cfg["sendgrid_api_key"] = os.getenv("SENDGRID_API_KEY", "")

    # Support both old single-date keys and new list keys for backward compat
    if "outbound_date" in cfg and "outbound_dates" not in cfg:
        cfg["outbound_dates"] = [cfg["outbound_date"]]
    if "return_date" in cfg and "return_dates" not in cfg:
        cfg["return_dates"] = [cfg["return_date"]] if cfg.get("return_date") else []

    # Validate required fields
    required = ["alert_email", "price_threshold_usd", "outbound_dates", "check_interval_hours"]
    for field in required:
        if not cfg.get(field):
            raise ValueError(f"Missing required field '{field}' in config.yaml.")

    # Defaults
    cfg.setdefault("return_dates", [])
    cfg.setdefault("currency", "EUR")
    cfg.setdefault("adults", 1)
    cfg.setdefault("carry_on_bags", 0)
    cfg.setdefault("checked_bags", 0)
    cfg.setdefault("smtp_host", "smtp.gmail.com")
    cfg.setdefault("smtp_port", 587)
    cfg.setdefault("origin_airports", ["TFS", "TFN"])
    cfg.setdefault("destination", "MIA")
    cfg.setdefault("max_alerts_per_day", 3)

    return cfg
