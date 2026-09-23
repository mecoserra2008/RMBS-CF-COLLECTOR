import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FX = ROOT / "fixtures"


def fx(name: str) -> str:
    return (FX / name).read_text(encoding="utf-8")
