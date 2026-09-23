"""Thin CLI: ``python -m rmbs.cli run|selfcheck|workbook [...]``."""
from __future__ import annotations

import sys


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv.pop(0) if argv else "run"
    if cmd == "run":
        from .run import main as m
    elif cmd == "selfcheck":
        from .selfcheck import main as m
    elif cmd == "workbook":
        from .workbook import main as m
    else:
        print("usage: python -m rmbs.cli run|selfcheck|workbook [options]")
        return 2
    return m(argv)


if __name__ == "__main__":
    sys.exit(main())
