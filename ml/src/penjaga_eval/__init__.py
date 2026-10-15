"""Penjaga M11: evaluation harness.

Truth is joined only inside the scorer, after detectors have produced their findings from
observed readings (proposal §3.2, principle 6). Replay reads nothing but an episode's
``observed/`` directory.
"""
