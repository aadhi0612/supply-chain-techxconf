"""Runtime settings, read from environment variables when `Settings.from_env()` is called."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    db_url: str = "sqlite:///siliconflow.db"
    mode: str = "draft"  # shadow | draft | execute
    outbox_dir: str = "outbox"
    webhook_url: str | None = None
    webhook_token: str | None = None
    use_llm: bool = False
    ledger_path: str = "ledger.db"  # where anchor roots are published; keep it outside the platform database
    demo: bool = False  # DEV ONLY: seeds sample data and exposes fixed, public API keys

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        return cls(
            db_url=e("SILICONFLOW_DB", cls.db_url), mode=e("SILICONFLOW_MODE", cls.mode), outbox_dir=e("SILICONFLOW_OUTBOX", cls.outbox_dir),
            webhook_url=e("SILICONFLOW_WEBHOOK_URL"), webhook_token=e("SILICONFLOW_WEBHOOK_TOKEN"),
            use_llm=bool(e("ANTHROPIC_API_KEY")) and e("SILICONFLOW_LLM", "1") != "0", ledger_path=e("SILICONFLOW_LEDGER", cls.ledger_path), demo=e("SILICONFLOW_DEMO") == "1",
        )
