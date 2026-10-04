from __future__ import annotations

import json
import os
from pathlib import Path

ENV = "ARTHASIGNAL_EXCLUSIONS"


def excluded_symbols() -> dict[str, str]:
    path = os.environ.get(ENV)
    if not path or not Path(path).exists():
        return {}
    data = json.loads(Path(path).read_text())
    return {str(k): str(v) for k, v in (data.get("symbols") or {}).items()}
