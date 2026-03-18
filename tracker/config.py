"""
config.py - Loads settings from .env (secrets) and config.yaml (user preferences).
"""

import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the project root (one level above this file)
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
            "Copy config.yaml.example to config.yaml and fill in your settings."
        )

    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f)

    # Inject secrets from environment
    cfg["serpapi_key"] = _require_env("SERPAPI_KEY")
    cfg["smtp_user"] = _require_env("SMTP_USER")
    cfg["smtp_password"] = _require_env("SMTP_PASSWORD")

    # Validate required yaml fields
    required_yaml = [
        "alert_email",
        "price_threshold_usd",
        "outbound_date",
        "check_interval_hours",
    ]
    for field in required_yaml:
        if field not in cfg:
            raise ValueError(
                f"Missing required field '{field}' in config.yaml."
            )

    # Optional fields with defaults
    cfg.setdefault("return_date", None)
    cfg.setdefault("currency", "USD")
    cfg.setdefault("adults", 1)
    cfg.setdefault("smtp_host", "smtp.gmail.com")
    cfg.setdefault("smtp_port", 587)
    cfg.setdefault("origin", "MIA")
    cfg.setdefault("destination", "TFS")
    cfg.setdefault("max_alerts_per_day", 3)

    return cfg
