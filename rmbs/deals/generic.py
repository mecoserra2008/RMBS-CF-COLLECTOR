def mode(deal, row):
    m = (deal.get("amortisation") or {}).get("mode")
    return "sequential" if m == "sequential" else None


def shortfall_allowed(deal):
    return bool(deal.get("principal_shortfall_option"))
