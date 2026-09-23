"""BCP / Magellan investor report (EN, comma or space thousands). Layout: 'bcp_investor_report'."""
from __future__ import annotations

import rmbs_harvester as H

from ._common import normalise


def parse(text: str, isin: str, url: str, *, series: str | None = None) -> list[dict]:
    if isin not in text:
        from . import review_row
        return [review_row(isin, url, "MISMATCH: ISIN not in report")]
    r = H.parse_bcp(text, isin, url)
    st = r["parse_status"]
    r["parse_status"] = "ok: issuer report, identities pass" if st == "ok" else st
    return [normalise(r)]
