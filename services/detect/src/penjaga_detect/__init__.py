"""Penjaga M5: detection services.

Detection consumes readings, events and the house contract only. It never imports ground truth
(``penjaga_contract.lab``), the simulator or the evaluation harness: import-linter enforces it.
"""
