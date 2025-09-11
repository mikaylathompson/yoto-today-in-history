from __future__ import annotations

import sys
import argparse

sys.path.append(".")

from src.app.utils.audio_store import delete_older_than  # noqa: E402
from src.app.config import settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete audio files older than retention period")
    parser.add_argument("--hours", type=int, default=settings.audio_retention_hours, help="Retention in hours")
    args = parser.parse_args()
    removed = delete_older_than(args.hours)
    print(f"Removed {removed} audio files older than {args.hours} hours")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


