"""Magellan 3/4: classes A-D pro-rata while the report's Pro-Rata Test passes, else sequential."""


def mode(deal, row):
    t = str(row.get("prorata_test", "")).upper()
    if t.startswith("PASS"):
        return "pro_rata"
    if t.startswith("FAIL"):
        return "sequential"
    return None


def shortfall_allowed(deal):
    return False
