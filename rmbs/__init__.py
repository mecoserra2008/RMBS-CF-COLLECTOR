"""RMBS payment-history pipeline: locate -> fetch -> extract -> parse -> validate -> gate -> select -> price -> report.

Rule: parsers never fetch, fetchers never parse, the engine never reads the network.
"""
__version__ = "1.0.0"
