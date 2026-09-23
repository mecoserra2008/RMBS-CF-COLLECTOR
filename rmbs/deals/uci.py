"""UCI 15/16: pro-rata unless the 90d+ arrears trigger is hit ('LAS SERIES B y C NO SE AMORTIZAN')."""


def mode(deal, row):
    t = str(row.get("prorata_test", "")).upper()
    if t.startswith("FAIL"):
        return "sequential"
    if t.startswith("PASS"):
        return "pro_rata"
    return None


def shortfall_allowed(deal):
    return False
