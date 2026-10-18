"""Short owner-facing labels per finding type, for timelines and "also seen" lines."""

from __future__ import annotations

FINDING_LABELS: dict[str, str] = {
    "night_flow_unexplained": "water flowing at night when nobody is using it",
    "dripping_fixture_suspected": "a dripping tap or fixture",
    "running_cistern_suspected": "a toilet cistern that keeps refilling",
    "tank_overflow_suspected": "the roof tank overflowing",
    "concealed_leak_suspected": "a possible hidden leak",
    "burst_pipe_suspected": "continuous high water flow (possible burst pipe)",
    "circuit_overload": "a circuit drawing more than its breaker is rated for",
    "earth_leakage_elevated": "electricity leaking to earth",
    "earth_leakage_rising": "earth leakage rising",
    "protective_device_trip": "a breaker or safety switch tripped",
    "repeated_trips": "the same breaker tripping repeatedly",
    "aircon_efficiency_decline": "an aircon using more power for the same cooling",
    "fridge_duty_cycle_rise": "the fridge running more than usual",
    "fridge_compressor_wear": "signs of fridge compressor wear",
    "water_supply_interruption": "the water supply stopped",
    "power_supply_interruption": "the power supply cut out",
}

SEVERITY_WORDS: dict[str, str] = {
    "info": "for information",
    "heads_up": "heads-up",
    "act_soon": "act soon",
    "urgent": "urgent",
}


def finding_label(finding_type: str) -> str:
    return FINDING_LABELS[finding_type]
