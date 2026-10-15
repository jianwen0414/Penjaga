/* GENERATED from packages/contract/src/penjaga_contract/schemas by packages/contract/tools/generate.py. Do not edit by hand. */

/**
 * Output of a detection service (M5): structured, no prose, no LLM. A finding is one condition tracked over time: it keeps its id across revisions, and the same condition (same key) never opens a second active finding. Monetary impact is not part of a finding; the M6 tariff calculator converts 'quantities'. Semantic rules (checked by penjaga_contract.validate): candidate probabilities plus candidates_other_p sum to at most 1; first_seen <= last_seen <= updated_at.
 */
export type Finding = {
  schema_version: "1.0";
  /**
   * Deterministic ID derived from key and first_seen, so replays produce identical findings.
   */
  id: string;
  /**
   * Dedupe key of the underlying condition, e.g. 'L2:circuit_overload:circ_sockets_kitchen'.
   */
  key: string;
  revision: number;
  status: FindingStatus;
  /**
   * Present when status is 'superseded'.
   */
  superseded_by?: string;
  cleared_at?: HouseTime;
  house_id: HouseId;
  layer: DetectionLayer;
  type: FindingType;
  severity: Severity;
  /**
   * True when the severity comes from a deterministic safety-floor rule. Nothing downstream may present this finding at a lower severity (PRD §15.3).
   */
  severity_floor: boolean;
  confidence: number;
  confidence_kind: ConfidenceKind;
  /**
   * Elements the finding is about with certainty (e.g. the monitored circuit). May be empty when the location is uncertain; see candidates.
   */
  object_ids: ObjectId[];
  /**
   * Possible locations/sources, most likely first.
   *
   * @maxItems 20
   */
  candidates: Candidate[];
  /**
   * Probability mass on locations not listed in candidates.
   */
  candidates_other_p: number;
  /**
   * Physical impact estimates keyed by snake_case name (validated semantically), e.g. water_l_per_day, extra_energy_kwh_per_day.
   */
  quantities: {
    [k: string]: Quantity;
  };
  /**
   * @minItems 1
   */
  evidence: [EvidenceItem, ...EvidenceItem[]];
  /**
   * Stream windows a client can fetch (as bounded summaries) to chart the evidence.
   */
  series_refs: SeriesRef[];
  onset_estimate?: HouseTime;
  first_seen: HouseTime;
  last_seen: HouseTime;
  updated_at: HouseTime;
  /**
   * Key of reviewed safety guidance text held in the knowledge base. Required for urgent findings.
   */
  safety_guidance_key?: string;
  source: Source;
};
export type FindingStatus = "active" | "cleared" | "superseded";
/**
 * Instant in house time (see M4): an RFC 3339 date-time, which always carries an explicit UTC offset (validators must assert the format). Services emit +08:00 (Asia/Kuala_Lumpur).
 */
export type HouseTime = string;
/**
 * ID of one house instance.
 */
export type HouseId = string;
/**
 * L4 (maintenance) items are produced from the knowledge base, not by detection.
 */
export type DetectionLayer = "L1" | "L2" | "L3" | "L5";
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "FindingType".
 */
export type FindingType =
  | "night_flow_unexplained"
  | "dripping_fixture_suspected"
  | "running_cistern_suspected"
  | "tank_overflow_suspected"
  | "concealed_leak_suspected"
  | "burst_pipe_suspected"
  | "circuit_overload"
  | "earth_leakage_elevated"
  | "earth_leakage_rising"
  | "protective_device_trip"
  | "repeated_trips"
  | "aircon_efficiency_decline"
  | "fridge_duty_cycle_rise"
  | "fridge_compressor_wear"
  | "water_supply_interruption"
  | "power_supply_interruption";
/**
 * Ordered severity scale. 'urgent' is reserved for safety (e.g. electrical hazard, burst pipe).
 */
export type Severity = "info" | "heads_up" | "act_soon" | "urgent";
/**
 * calibrated_probability: model score calibrated on held-out validation data (reported in M11). deterministic_rule: a measured condition crossed a documented threshold; confidence is 1.
 */
export type ConfidenceKind = "calibrated_probability" | "deterministic_rule";
/**
 * Stable, unique, human-readable ID of a physical element: <kind-prefix>_<descriptor>, lowercase snake_case. The prefix must match the element kind (enforced by the semantic validator).
 */
export type ObjectId = string;
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "EvidenceText".
 */
export type EvidenceText = string;
/**
 * Concrete stream ID from the stream catalogue (streams.json), dot-separated, e.g. elec.circuit.circ_ac_master.current_a.
 */
export type StreamId = string;
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "Source".
 */
export type Source = Source1;
export type SourceKind = "rule" | "model" | "baseline";
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "L1FindingType".
 */
export type L1FindingType =
  | "night_flow_unexplained"
  | "dripping_fixture_suspected"
  | "running_cistern_suspected"
  | "tank_overflow_suspected"
  | "concealed_leak_suspected"
  | "burst_pipe_suspected";
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "L2FindingType".
 */
export type L2FindingType =
  "circuit_overload" | "earth_leakage_elevated" | "earth_leakage_rising" | "protective_device_trip" | "repeated_trips";
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "L3FindingType".
 */
export type L3FindingType = "aircon_efficiency_decline" | "fridge_duty_cycle_rise" | "fridge_compressor_wear";
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "L5FindingType".
 */
export type L5FindingType = "water_supply_interruption" | "power_supply_interruption";

/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "Candidate".
 */
export interface Candidate {
  object_id: ObjectId;
  p: number;
  /**
   * Machine-readable reason codes joined by '+', e.g. 'periodic_refill+tank_fed'.
   */
  basis: string;
}
/**
 * A physical quantity with unit and optional 90% interval. Monetary values are never computed by detection; the M6 tariff calculator converts physical quantities to RM.
 */
export interface Quantity {
  value: number;
  unit: string;
  /**
   * @minItems 2
   * @maxItems 2
   */
  ci90?: [number, number];
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "EvidenceItem".
 */
export interface EvidenceItem {
  name: string;
  /**
   * Numeric measurement, short machine-readable label, or flag.
   */
  value: number | EvidenceText | boolean;
  unit?: string;
  baseline?: number;
  threshold?: number;
  window?: TimeWindow;
}
/**
 * Half-open house-time interval [from, to).
 */
export interface TimeWindow {
  from: HouseTime;
  to: HouseTime;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "SeriesRef".
 */
export interface SeriesRef {
  stream: StreamId;
  window: TimeWindow;
  bucket_s: number;
}
export interface Source1 {
  detector: string;
  kind: SourceKind;
  model?: string;
  version: string;
  /**
   * Required for trained models (PRD §15.1).
   */
  model_card?: string;
}
