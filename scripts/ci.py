#!/usr/bin/env python3
"""Local CI check — same steps as .github/workflows/ci.yml"""

import subprocess
import sys

CHECKS = [
    (["ruff", "check", "."], "ruff check"),
    (["ruff", "format", "--check", "."], "ruff format"),
    (["mypy", "server.py"], "mypy"),
    (["pytest", "tests/", "--tb=short", "-q"], "tests"),
]

GREEN = "\033[0;32m"
RED = "\033[0;31m"
BOLD = "\033[1m"
RESET = "\033[0m"


def main() -> None:
    print(f"{BOLD}Running CI checks...{RESET}\n")
    for cmd, label in CHECKS:
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"\n{RED}✗{RESET} {label}")
            sys.exit(1)
        print(f"{GREEN}✓{RESET} {label}\n")
    print(f"{GREEN}{BOLD}All checks passed.{RESET}")


if __name__ == "__main__":
    main()
