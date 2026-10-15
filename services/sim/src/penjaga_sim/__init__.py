"""Penjaga M2: house simulator."""

# Identifies the generator's output, not the package release. Bump it whenever the same seed
# and manifest would produce different readings, events or truth; the fingerprint test in
# tests/test_determinism.py fails until you do. Episode manifests record it, and regenerating a
# manifest made by another generator version is refused.
GENERATOR_VERSION = "penjaga-sim/1"
