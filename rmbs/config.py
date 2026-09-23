"""Run and deal configuration. A missing deal field is CONFIG_INCOMPLETE for that ISIN, never a silent default."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

# Fields the waterfall/engine needs (PIPELINE_SPEC section 4). ``None`` in the YAML = unknown.
REQUIRED_DEAL_FIELDS = ["isin", "deal", "series", "original_balance", "notes", "denomination", "index", "margin_bp",
                        "margin_steps", "coupon_floor", "day_basis", "ipd_rule", "amortisation", "triggers",
                        "reserve", "clean_up_call_pct", "legal_final", "swap", "fees", "sources"]


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as h:
        return yaml.safe_load(h) or {}


def load_run(path: str | Path) -> dict:
    p = Path(path)
    cfg = load_yaml(p if p.is_absolute() else ROOT / p)
    cfg.setdefault("root", str(ROOT))
    return cfg


def load_deal(isin: str, deals_dir: Path | None = None) -> dict:
    p = (deals_dir or ROOT / "config" / "deals") / f"{isin}.yaml"
    return load_yaml(p) if p.exists() else {"isin": isin}


def missing_fields(deal: dict) -> list[str]:
    miss = []
    for k in REQUIRED_DEAL_FIELDS:
        v = deal.get(k)
        if v is None or v == "":
            miss.append(k)
    rule = deal.get("ipd_rule")
    rules = rule if isinstance(rule, list) else [rule] if rule else []
    for r in rules:
        if not isinstance(r, dict) or not r.get("months") or not r.get("day"):
            miss.append("ipd_rule.months/day")
    return sorted(set(miss))


def ipd_rules(deal: dict) -> list[dict]:
    r = deal.get("ipd_rule")
    if not r:
        return []
    return r if isinstance(r, list) else [r]


def config_status(deal: dict) -> str:
    miss = missing_fields(deal)
    return "COMPLETE" if not miss else "CONFIG_INCOMPLETE: " + ",".join(miss)
