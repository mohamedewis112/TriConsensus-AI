from __future__ import annotations

import argparse
import json
from pathlib import Path

from .regime_replication import compare_regime_reports


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build fail-closed replicated regime policy"
    )
    parser.add_argument("--discovery-report", required=True)
    parser.add_argument("--replication-report", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    discovery = json.loads(Path(args.discovery_report).read_text(encoding="utf-8"))
    replication = json.loads(Path(args.replication_report).read_text(encoding="utf-8"))
    policy = compare_regime_reports(discovery, replication)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(policy, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(policy, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
