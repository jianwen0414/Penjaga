"""Penjaga M2: house simulator."""

# Identifies the generator's output, not the package release. Bump it whenever the same seed
# and manifest would produce different readings, events or truth; the fingerprint test in
# services/sim/tests/test_world.py fails until you do. Episode manifests record it, and
# regenerating a manifest made by another generator version is refused.
#
# penjaga-sim/2: night-time toilet visits (DS-026, DS-027).
GENERATOR_VERSION = "penjaga-sim/2"
