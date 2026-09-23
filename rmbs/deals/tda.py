"""TdA funds (TDA CAM 5-9, Madrid RMBS I): sequential; TDA CAM 8 pro-rata only while pool factor >= 10%."""


def mode(deal, row):
    am = deal.get("amortisation") or {}
    if am.get("mode") == "sequential":
        return "sequential"
    if am.get("mode") == "pro_rata_while_pool_factor_ge":
        t = str(row.get("prorata_test", "")).upper()
        return "pro_rata" if t.startswith("PASS") else "sequential" if t.startswith("FAIL") else None
    return None


def shortfall_allowed(deal):
    return False
