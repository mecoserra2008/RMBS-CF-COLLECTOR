"""Santander de Titulizacion 'Informacion Periodica' (UCI 15/16). Layout: 'uci_informacion_periodica'."""
from __future__ import annotations

import rmbs_harvester as H

from ._common import normalise


def parse(text: str, isin: str, url: str, *, series: str | None = None) -> list[dict]:
    from . import review_row
    if series not in (None, "", "A"):
        # The tested regexes read 'B.T.A'S SERIE A'. UCI 16 A2 needs its own fixture before it is trusted.
        return [review_row(isin, url, f"LAYOUT_UNKNOWN: series {series} not covered by the UCI fixture set")]
    r = H.parse_uci(text, isin, url)
    st = r["parse_status"]
    r["parse_status"] = "ok: issuer report, identities pass" if st == "ok" else st
    return [normalise(r)]
