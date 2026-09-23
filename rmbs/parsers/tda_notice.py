"""TdA 'INFORMACION A LOS INVERSORES - FECHA DE PAGO' notice (CNMV OIR). Layout: 'tda_notice'."""
from __future__ import annotations

import rmbs_harvester as H

from ._common import normalise


def parse(text: str, isin: str, url: str, *, series: str | None = None) -> list[dict]:
    from . import review_row
    r = H.parse_tda_notice(text, isin, url)
    if r is None:
        return [review_row(isin, url, "MISMATCH: ISIN not in notice")]
    if not str(r["parse_status"]).startswith("ok"):
        r["parse_status"] = "PARTIAL: TdA notice, balance identity not closed (fields missing in text)"
    return [normalise(r)]
