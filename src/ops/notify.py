from __future__ import annotations

import argparse
import subprocess


def main() -> None:
    parser = argparse.ArgumentParser(description="Post a failed systemd unit and its last log lines to Discord")
    parser.add_argument("--unit", required=True)
    args = parser.parse_args()
    from src.notifications.discord_alert import send_discord_alert

    try:
        tail = subprocess.run(["journalctl", "-u", args.unit, "-n", "25", "--no-pager", "-o", "cat"], capture_output=True, text=True,
                              timeout=30).stdout
    except Exception as error:
        tail = f"(journal unavailable: {error})"
    send_discord_alert(f"systemd unit **{args.unit}** failed.\n```\n{tail[-3000:]}\n```", "failure")


if __name__ == "__main__":
    main()
