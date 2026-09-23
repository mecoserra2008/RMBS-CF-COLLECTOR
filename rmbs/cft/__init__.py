"""Bloomberg CFT-convention projection engine, exhaustive configuration grid, and smoothness ranking.

conventions.py  prepay (CPR, SMM, PSA, ABS) / default (CDR, MDR, SDA) vectors exactly as the market standards define them
engine.py       monthly collateral -> quarterly waterfall, vectorised over configurations (numpy)
grid.py         every CFT setting combination for an ISIN (config/cft.yaml)
smooth.py       smoothness score: continuity with the filed paydown history at the seam + roughness after it
criterion.py    out-of-sample check: does 'smoother' actually mean 'closer to the realised cash flows'?
"""
