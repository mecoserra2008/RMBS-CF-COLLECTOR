"""Hipocat 9/11 (EdT): A-series pro-rata subject to the accounts' conditions; principal due may exceed principal paid
('insuficiencia de fondos disponibles'), the unpaid part is carried to the next IPD."""


def mode(deal, row):
    t = str(row.get("prorata_test", "")).upper()
    if t.startswith("PASS"):
        return "pro_rata"
    if t.startswith("FAIL"):
        return "sequential"
    return None


def shortfall_allowed(deal):
    return bool(deal.get("principal_shortfall_option"))
