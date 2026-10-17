"""Penjaga M2: house simulator."""

# Identifies the generator's output, not the package release. Bump it whenever the same seed
# and manifest would produce different readings, events or truth; the fingerprint test in
# services/sim/tests/test_world.py fails until you do. Episode manifests record it, and
# regenerating a manifest made by another generator version is refused.
#
# penjaga-sim/2: night-time toilet visits (DS-026, DS-027).
# penjaga-sim/3: RO purifier refills (DS-029 to DS-032); meter cutoff from 1.4 L/h (DS-028).
# penjaga-sim/4: the purifier's heater and compressor load (DS-036, DS-037); the imposable
#   low-supply-pressure anomaly (post_outage_tank_refill, pattern daily_low_pressure; DS-033 to
#   DS-035); guided-check actions; per-minute water truth.
GENERATOR_VERSION = "penjaga-sim/4"
