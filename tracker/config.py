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


def _as_list(value, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise ValueError(f"'{field_name}' must be a string or a list of strings.")


def load_config() -> dict:
    config_path = ROOT / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(
            f"config.yaml not found at {config_path}. "
            "Create config.yaml and copy .env.example to .env."
        )

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    cfg["email_provider"] = os.getenv("EMAIL_PROVIDER", "sendgrid").strip().lower()
    cfg["sender_email"] = os.getenv("SENDER_EMAIL", "")
    cfg["sender_name"] = os.getenv("SENDER_NAME", "Flight Tracker")
    if not cfg["sender_email"]:
        raise EnvironmentError(
            "Required environment variable 'SENDER_EMAIL' is not set. "
        )
    if cfg["email_provider"] == "sendgrid":
        cfg["sendgrid_api_key"] = _require_env("SENDGRID_API_KEY")
    elif cfg["email_provider"] in {"smtp", "gmail"}:
        cfg["smtp_host"] = os.getenv("SMTP_HOST", "smtp.gmail.com")
        cfg["smtp_port"] = int(os.getenv("SMTP_PORT", "465"))
        cfg["smtp_user"] = _require_env("SMTP_USER")
        cfg["smtp_password"] = _require_env("SMTP_PASSWORD")
    else:
        raise ValueError(
            "EMAIL_PROVIDER must be either 'sendgrid' or 'smtp'. "
            "Use 'smtp' for Gmail/local app-password sending."
        )

    # Flight source credentials. Empty values simply disable that source.
    cfg["serpapi_key"] = os.getenv("SERPAPI_KEY", "")
    cfg["kiwi_api_key"] = os.getenv("KIWI_API_KEY", "")
    cfg["rapidapi_key"] = os.getenv("RAPIDAPI_KEY", "")
    cfg["aviasales_token"] = os.getenv("AVIASALES_TOKEN", "")

    # Convenience aliases for single-route configs.
    if "outbound_date" in cfg and "outbound_dates" not in cfg:
        cfg["outbound_dates"] = [cfg["outbound_date"]]
    if "return_date" in cfg and "return_dates" not in cfg:
        cfg["return_dates"] = [cfg["return_date"]] if cfg.get("return_date") else []
    if "origins" in cfg:
        cfg["origin_airports"] = _as_list(cfg["origins"], "origins")
    elif "origin_airports" in cfg:
        cfg["origin_airports"] = _as_list(cfg["origin_airports"], "origin_airports")
    elif "origin" in cfg:
        cfg["origin_airports"] = _as_list(cfg["origin"], "origin")

    if "destinations" in cfg:
        cfg["destination_airports"] = _as_list(cfg["destinations"], "destinations")
    elif "destination_airports" in cfg:
        cfg["destination_airports"] = _as_list(cfg["destination_airports"], "destination_airports")
    elif "destination" in cfg:
        cfg["destination_airports"] = _as_list(cfg["destination"], "destination")

    required = [
        "alert_email",
        "price_threshold",
        "outbound_dates",
        "origin_airports",
        "destination_airports",
    ]
    for field in required:
        if not cfg.get(field):
            raise ValueError(f"Missing required field '{field}' in config.yaml.")

    cfg["outbound_dates"] = _as_list(cfg["outbound_dates"], "outbound_dates")
    cfg["return_dates"] = _as_list(cfg.get("return_dates", []), "return_dates")

    cfg.setdefault("return_dates", [])
    cfg.setdefault("currency", "EUR")
    cfg.setdefault("adults", 1)
    cfg.setdefault("carry_on_bags", 0)
    cfg.setdefault("checked_bags", 0)
    cfg.setdefault("max_alerts_per_day", 3)
    cfg.setdefault("send_summary_when_no_alert", True)
    cfg.setdefault("google_interval_hours", 12)
    cfg.setdefault("kiwi_interval_hours", 60)
    cfg.setdefault("skyscanner_interval_hours", 72)
    cfg.setdefault("aviasales_interval_hours", 24)
    cfg.setdefault("initial_sources", ["google", "aviasales"])

    return cfg
