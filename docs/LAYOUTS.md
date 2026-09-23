# Document layouts (parser catalogue)

Every layout has a fixture in `fixtures/` and exact-number assertions in `tests/test_parsers.py` (and, for the
original five, in `python rmbs_harvester.py --selftest`). `rmbs.parsers.detect(text)` recognises the layout;
unknown layouts return `review: LAYOUT_UNKNOWN` and go to the layout queue.

| Layout id | Recognised by | Parser | Fixtures | Units |
|---|---|---|---|---|
| `bcp_investor_report` | "Security Level Information" + "Magellan Mortgages" | `parsers/bcp.py` -> `rmbs_harvester.parse_bcp` | magel3_2009-02 (comma thousands), magel3_2025-11 (space thousands), magel3_2026-05/-08, magel4_2026-01/-07 | EUR, `1,234.56` or `1 234.56` |
| `uci_informacion_periodica` | "VALORES EMITIDOS" or "B.T.A'S SERIE" | `parsers/uci.py` -> `parse_uci` (Serie A only; other series -> review until a fixture exists) | uci15_2012-06, uci15_2026-06 | EUR, `1.234,56` |
| `edt_accounts` | "Amortizacion dd.mm.yyyy" + "Serie" | `parsers/edt_accounts.py` (Nota 'movimiento de los Bonos', 2 columns per series; optional per-date "Devengado / Liquidado / Insuficiencia" lines -> principal_due / principal_shortfall) | hipo11_ca2016 (negative in parentheses), hipo11_ca2019 (leading minus, dates on the next line) | kEUR, `1.234` |
| `tda_accounts` | "Liquidacion de pagos de las liquidaciones intermedias" | `parsers/tda_accounts.py` (row `Pagos por amortizacion ordinaria SERIE <x>`; chain from "Saldo inicial") | tdacam5_ca2022 (`.` and space thousands, values split over lines) | kEUR |
| `tda_notice` | "FECHA DE PAGO: d de mes de yyyy" + "INFORMACION A LOS INVERSORES" | `parsers/tda_notice.py` (numbered items inside the ISIN block) | tdacam8_2020-11 (partial text) | EUR, `1.234,56` |
| `santander_accounts` | "U.C.I. 15/16" + "cuentas anuales" | `parsers/santander_accounts.py` - **no fixture yet: routes to the layout queue** | - | - |

Multi-series accounts are parsed only for the series mapped in `config/deals/<isin>.yaml` (`series`), taken from the
CNMV prospectus / BME listing: TDA CAM 6 = A3, TDA CAM 7 = A2 (ES0377994019) and A3 (ES0377994027), TDA CAM 9 = A2,
Madrid RMBS I = A2, Hipocat 9 = A2a / A2b, Hipocat 11 = A2, UCI 16 = A2.

`captures/<ISIN>_<ticker>/*.txt`: text captured from issuer PDFs in the original session when the PDF itself could
not be stored. First line `# source: <url>`; parsed exactly like extracted PDF text.
