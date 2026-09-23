"""TdA annual accounts: 'Liquidacion de pagos de las liquidaciones intermedias' (kEUR). Layout CA2022 (TDA CAM 5).

Multi-series funds: the ISIN -> 'SERIE x' label must come from the prospectus mapping (config/deals/<isin>.yaml
``series``); an unmapped ISIN is never parsed (returns review: SERIES_UNMAPPED)."""
from __future__ import annotations

import rmbs_harvester as H

from ._common import normalise


def parse(text: str, isin: str, url: str, *, series: str | None = None) -> list[dict]:
    from . import review_row
    if not series:
        return [review_row(isin, url, "SERIES_UNMAPPED: map ISIN -> SERIE from the CNMV prospectus first")]
    rows = H.parse_tda_accounts(text, isin, url, serie=series)
    if not rows:
        return [review_row(isin, url, f"LAYOUT_UNKNOWN: no 'Pagos por amortizacion ordinaria SERIE {series}' row")]
    return [normalise(r) for r in rows]
