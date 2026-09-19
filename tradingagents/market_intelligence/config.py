"""Safe defaults for optional market intelligence."""
from __future__ import annotations

import os


def enabled(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def settings() -> dict[str, object]:
    return {
        "external_data_enabled": enabled("HL_EXTERNAL_DATA_ENABLED"),
        "external_context_enabled": enabled("HL_EXTERNAL_CONTEXT_ENABLED"),
        "openbb_enabled": enabled("OPENBB_ENABLED"),
        "massive_enabled": enabled("MASSIVE_ENABLED"),
        "massive_base_url": os.getenv("MASSIVE_API_BASE_URL", "https://api.massive.com"),
        "context_max_tokens": min(max(int(os.getenv("HL_EXTERNAL_CONTEXT_MAX_TOKENS", "1500")), 256), 4000),
        "timeout_s": min(max(float(os.getenv("MASSIVE_TIMEOUT_S", "5")), 0.1), 30),
        "cache_ttl_s": min(max(int(os.getenv("MASSIVE_CACHE_TTL_S", "300")), 1), 3600),
    }
