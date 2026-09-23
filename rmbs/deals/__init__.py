"""Per-deal waterfall rules. Each module exposes ``mode(deal, row) -> 'pro_rata'|'sequential'|None`` (the
amortisation mode the prospectus prescribes given the trigger state printed in that IPD's filing) and
``shortfall_allowed(deal) -> bool``. ``None`` = the filing does not carry what the rule needs."""
from __future__ import annotations

from . import generic, hipocat, magellan, tda, uci

BY_FAMILY = {"bcp": magellan, "tda": tda, "santander": uci, "edt": hipocat}


def module_for(deal: dict):
    return BY_FAMILY.get(deal.get("family"), generic)


def mode(deal: dict, row: dict) -> str | None:
    return module_for(deal).mode(deal, row)


def shortfall_allowed(deal: dict) -> bool:
    return module_for(deal).shortfall_allowed(deal)
