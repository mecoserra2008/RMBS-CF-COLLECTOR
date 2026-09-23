# Section-1 data read from the issuer reports (search-index text of the PDF). Balance = Denomination x 141,375 notes
# (exact: verified on every fully-read report, e.g. 873.92 x 141,375 = 123,550,440.00). Interest = beg x rate x days/360.
import datetime as dt
B = "https://ind.millenniumbcp.pt/pt/Institucional/investidores/securitizacoes/Documents/"
M3 = [  # pay, start, days, index, coupon, denom_beg, denom_end, coll_beg, coll_beg_net, coll_princ, file
 ("2013-08-16","2013-05-15",93,.00203,.00463,3535.33,3436.90,560443918.16,538171265.99,14433918.40,"201308Magellan3_InvestorReport.pdf"),
 ("2017-08-16","2017-05-15",93,-.00329,-.00069,2506.92,2445.08,403717641.83,381618414.91,10404854.52,"201708Magellan3_InvestorReport.pdf"),
 ("2018-08-16","2018-05-15",93,-.00327,0.0,2240.34,2176.38,359635569.80,341042639.38,10397493.23,"201808Magellan3_InvestorReport.pdf"),
 ("2018-11-15","2018-08-16",91,-.00319,0.0,2176.38,2118.77,349238076.57,331305203.31,9239055.35,"201811Magellan3_InvestorReport.pdf"),
 ("2019-02-15","2018-11-15",92,-.00316,0.0,2118.77,2059.95,339999021.22,322536131.04,9846787.50,"Investor_Report_2019_02.pdf"),
 ("2019-05-15","2019-02-15",89,-.00308,0.0,2059.95,2003.57,330152233.72,313582425.45,9206191.67,"Investor_Report_16052019.pdf"),
 ("2019-08-16","2019-05-15",93,-.00311,0.0,2003.57,1950.93,320946042.05,304998938.36,8347958.20,"Investor_Report_2019_08.pdf"),
 ("2020-11-16","2020-08-17",91,-.00481,0.0,1764.23,1722.64,281959472.54,268565770.73,6580273.84,"Investor_Report_2020_11_12112020.pdf"),
 ("2021-02-15","2020-11-16",91,-.00513,0.0,1722.64,1673.53,275379198.70,262233756.71,8134725.28,"Investor_Report_15022021.pdf"),
 ("2022-05-16","2022-02-15",90,-.00523,0.0,1491.80,1449.70,238424919.41,227094594.34,6770006.23,"InvestorReport_202205.pdf"),
 ("2023-05-15","2023-02-15",89,.02654,.02914,1300.03,1247.73,208555011.21,197901478.34,8123854.81,"InvestorReport_2023-05.pdf"),
 ("2023-11-15","2023-08-16",91,.03781,.04041,1205.89,1161.50,193919061.81,183571020.01,6891967.79,"Magellan3_InvestorReport_202311.pdf"),
 ("2024-05-15","2024-02-15",90,.03912,.04172,1116.02,1080.63,180050106.15,169890272.90,5555546.81,"InvestorReport_202405.pdf"),
 ("2025-02-17","2024-11-15",94,.03023,.03283,1012.86,972.65,163985388.24,154187031.05,6203280.72,"InvestorReport_202502.pdf"),
 ("2025-08-18","2025-05-15",95,.02143,.02403,935.90,902.75,151853398.98,142471768.17,5076114.59,"InvestorReport_202508.pdf"),
]
M4 = [
 ("2010-01-20","2009-10-20",92,.00739,.00879,5669.23,5428.82,865936119.32,864468918.29,36238567.41,"201001Magellan4_InvestorReport.pdf",14),
 ("2012-04-20","2012-01-20",91,.01204,.01344,4544.85,4441.31,700677123.52,692994863.96,14378657.86,"201204Magellan4_InvestorReport.pdf",14),
 ("2013-04-22","2013-01-21",91,.00204,.00344,4122.71,4011.98,639197916.66,628731932.50,16292520.20,"201304Magellan4_InvestorReport.pdf",14),
 ("2015-07-20","2015-04-20",91,.00002,.00142,3376.70,3310.80,529710288.15,514961816.62,9501622.58,"201507Magellan4_InvestorReport.pdf",14),
 ("2017-04-20","2017-01-20",90,-.00329,0.0,2867.62,2800.17,453369098.65,437324259.06,10752517.95,"201704Magellan4_InvestorReport.pdf",28),
 ("2018-04-20","2018-01-22",88,-.00328,0.0,2596.71,2534.51,410014650.72,396009802.16,9765179.34,"201804Magellan4_InvestorReport.pdf",28),
 ("2025-01-20","2024-10-21",91,.03219,.03499,1202.83,1164.46,189565807.16,183435096.80,5879318.24,"202501_Magellan4_InvestorReport.pdf",28),
]
# single-IPD gaps closed by the denomination chain (both neighbouring reports read)
M3_CHAIN = [("2023-08-16","2023-05-15",1247.73,1205.89),("2024-02-15","2023-11-15",1161.50,1116.02),
            ("2025-05-15","2025-02-17",972.65,935.90)]
N = 141375; ORIG = 1413750000.0
def rows(SCHEMA):
    out = []
    def base(isin, deal, m):
        r = {c: "" for c in SCHEMA}; r.update(isin=isin, deal=deal, tranche="A", original_balance=ORIG, n_notes=N,
                                           day_basis="Act/360", margin_bp=m); return r
    for p,s,d,ix,cp,db,de,cb,cbn,cpr,f in M3:
        r = base("XS0222684655","Magellan Mortgages No. 3",26)
        beg, end = round(db*N,2), round(de*N,2)
        r.update(payment_date=p, report_month=p[:7], accrual_start=s, accrual_end=p, accrual_days=d, index_rate=ix,
                 coupon_rate=cp, denom_beg=db, denom_end=de, beg_balance=beg, end_balance=end, principal_paid=round(beg-end,2),
                 principal_per_note=round(db-de,2), pool_factor=round(end/ORIG,10), interest_paid=round(beg*cp*d/360,2),
                 coll_beg_balance=cb, coll_principal_total=cpr, source_url=B+"Magellan3-InvestorReport/"+f,
                 source_section="Sec.1 Denomination / New Denomination / Accrual Rate; Sec.2 beginning balance & principal redemption",
                 parse_status="ok: issuer report Sec.1 (balances = denomination x 141,375; interest computed)")
        out.append(r)
    for p,s,d,ix,cp,db,de,cb,cbn,cpr,f,m in M4:
        r = base("XS0260784318","Magellan Mortgages No. 4",m)
        beg, end = round(db*N,2), round(de*N,2)
        r.update(payment_date=p, report_month=p[:7], accrual_start=s, accrual_end=p, accrual_days=d, index_rate=ix,
                 coupon_rate=cp, denom_beg=db, denom_end=de, beg_balance=beg, end_balance=end, principal_paid=round(beg-end,2),
                 principal_per_note=round(db-de,2), pool_factor=round(end/ORIG,10), interest_paid=round(beg*cp*d/360,2),
                 coll_beg_balance=cb, coll_principal_total=cpr, source_url=B+"Magellan4-InvestorReport/"+f,
                 source_section="Sec.1 Denomination / New Denomination / Accrual Rate; Sec.2 beginning balance & principal redemption",
                 parse_status="ok: issuer report Sec.1 (balances = denomination x 141,375; interest computed)")
        out.append(r)
    for p,s,db,de in M3_CHAIN:
        r = base("XS0222684655","Magellan Mortgages No. 3",26)
        beg, end = round(db*N,2), round(de*N,2)
        r.update(payment_date=p, report_month=p[:7], accrual_start=s, accrual_end=p, denom_beg=db, denom_end=de,
                 beg_balance=beg, end_balance=end, principal_paid=round(beg-end,2), principal_per_note=round(db-de,2),
                 pool_factor=round(end/ORIG,10), source_url="derived: denomination chain between neighbouring reports",
                 source_section="denomination chain", parse_status="DERIVED (principal exact; coupon not available)")
        out.append(r)
    return out
