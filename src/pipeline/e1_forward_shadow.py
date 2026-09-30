from __future__ import annotations

import argparse
import json

from src.database.connection import get_session
from src.services.quant_e1_forward import capture_e1_forward_decisions, build_e1_forward_validation_status


def run_e1_forward_shadow(*, limit: int = 300) -> dict:
    with get_session() as session:
        capture = capture_e1_forward_decisions(session, limit=limit)
    with get_session() as session:
        validation = build_e1_forward_validation_status(session)
    return {"capture": capture, "validation": validation}


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture and replay frozen E1 forward execution evidence")
    parser.add_argument("--limit", type=int, default=300)
    args = parser.parse_args()
    print(json.dumps(run_e1_forward_shadow(limit=args.limit), indent=2, default=str))


if __name__ == "__main__":
    main()
