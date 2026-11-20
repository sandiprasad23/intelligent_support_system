import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def env_value(name: str, *, allow_empty: bool = False) -> str:
    """Return a required setting loaded from the environment/.env file."""
    value = os.getenv(name)
    if value is None or (not allow_empty and not value.strip()):
        raise RuntimeError(f"Required configuration value {name} is missing from .env")
    return value


def env_float(name: str, *, safe_value: float | None = None) -> float:
    """Parse a required float setting; optionally return a fail-closed sentinel."""
    try:
        value = float(env_value(name))
    except (RuntimeError, TypeError, ValueError):
        if safe_value is not None:
            return safe_value
        raise
    return value
