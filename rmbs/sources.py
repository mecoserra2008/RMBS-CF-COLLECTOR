"""Registry: ISIN -> deal, manager, source family, NIF, series, output file."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Source:
    isin: str
    ticker: str            # file-name ticker, e.g. MAGEL_3_A
    deal: str
    series: str            # series label as printed in the filings ("" = not yet mapped)
    family: str            # bcp | santander | edt | tda | manual
    manager: str
    nif: str = ""
    code: str = ""         # BCP folder code / EdT fund code / Santander slug
    fund_key: str = ""     # groups ISINs of the same fund (multi-series accounts)
    notes: tuple = field(default_factory=tuple)

    @property
    def csv_name(self) -> str:
        return f"{self.isin}_{self.ticker}.csv"


REGISTRY: dict[str, Source] = {s.isin: s for s in [
    Source("XS0222684655", "MAGEL_3_A", "Magellan Mortgages No. 3 plc", "A", "bcp", "BCP", code="Magellan3", fund_key="MAGEL3"),
    Source("XS0260784318", "MAGEL_4_A", "Magellan Mortgages No. 4 plc", "A", "bcp", "BCP", code="Magellan4", fund_key="MAGEL4"),
    Source("XS0230694233", "LUSI_4_A", "Lusitano Mortgages No. 4 plc", "A", "manual", "Citibank N.A. / novobanco", fund_key="LUSI4"),
    Source("XS0268642161", "LUSI_5_A", "Lusitano Mortgages No. 5 plc", "A", "manual", "Citibank N.A. / novobanco", fund_key="LUSI5"),
    Source("XS0312981649", "LUSI_6_A", "Lusitano Mortgages No. 6 plc", "A", "manual", "Citibank N.A. / novobanco", fund_key="LUSI6"),
    Source("ES0377992005", "TDAC_5_A", "TDA CAM 5, FTA", "A", "tda", "Titulizacion de Activos SGFT", nif="V84466135", fund_key="TDACAM5"),
    Source("ES0377993029", "TDAC_6_A3", "TDA CAM 6, FTA", "A3", "tda", "Titulizacion de Activos SGFT", nif="V84664358", fund_key="TDACAM6"),
    Source("ES0377994019", "TDAC_7_A2", "TDA CAM 7, FTA", "A2", "tda", "Titulizacion de Activos SGFT", nif="V84851724", fund_key="TDACAM7"),
    Source("ES0377994027", "TDAC_7_A3", "TDA CAM 7, FTA", "A3", "tda", "Titulizacion de Activos SGFT", nif="V84851724", fund_key="TDACAM7"),
    Source("ES0377966009", "TDAC_8_A", "TDA CAM 8, FTA", "A", "tda", "Titulizacion de Activos SGFT", nif="V85017986", fund_key="TDACAM8"),
    Source("ES0377955010", "TDAC_9_A2", "TDA CAM 9, FTA", "A2", "tda", "Titulizacion de Activos SGFT", nif="V85151918", fund_key="TDACAM9"),
    Source("ES0359091016", "CAJAM_2006-1_A2", "MADRID RMBS I, FTA", "A2", "tda", "Titulizacion de Activos SGFT", nif="V84889229", fund_key="MADRID1"),
    Source("ES0380957003", "UCI_15_A", "F.T.A. U.C.I. 15", "A", "santander", "Santander de Titulizacion SGFT", nif="V84698067", code="uci-15", fund_key="UCI15"),
    Source("ES0338186010", "UCI_16_A2", "F.T.A. U.C.I. 16", "A2", "santander", "Santander de Titulizacion SGFT", nif="V84856236", code="uci-16", fund_key="UCI16"),
    Source("ES0345672010", "HIPO_11_A2", "HIPOCAT 11, FTA", "A2", "edt", "Europea de Titulizacion", nif="V64478373", code="FGH11", fund_key="HIPO11"),
    Source("ES0345721015", "HIPO_9_A2a", "HIPOCAT 9, FTA", "A2a", "edt", "Europea de Titulizacion", nif="V64006075", code="FGH09", fund_key="HIPO9"),
    Source("ES0345721023", "HIPO_9_A2b", "HIPOCAT 9, FTA", "A2b", "edt", "Europea de Titulizacion", nif="V64006075", code="FGH09", fund_key="HIPO9"),
]}

MANUAL = {i for i, s in REGISTRY.items() if s.family == "manual"}


def get(isin: str) -> Source:
    return REGISTRY[isin]


def series_for(isin: str, deal_cfg: dict | None = None) -> str:
    """Series label from the deal config (prospectus mapping) first, registry second. '' = unmapped: multi-series
    accounts must not be parsed for this ISIN until the mapping is confirmed."""
    if deal_cfg and deal_cfg.get("series"):
        return str(deal_cfg["series"])
    return REGISTRY[isin].series
