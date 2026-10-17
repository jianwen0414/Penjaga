/* GENERATED from packages/contract/src/penjaga_contract/schemas by packages/contract/tools/generate.py. Do not edit by hand. */

/**
 * Preventive layer: L1 leaks, L2 electrical safety, L3 appliance health, L4 maintenance, L5 management integration.
 */
export type Layer = "L1" | "L2" | "L3" | "L4" | "L5";
export type SourceKind = "rule" | "model" | "baseline";
/**
 * Instant in house time (see M4): an RFC 3339 date-time, which always carries an explicit UTC offset (validators must assert the format). Services emit +08:00 (Asia/Kuala_Lumpur).
 */
export type HouseTime = string;
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
  safety_guidance_key?: SafetyGuidanceKey;
  source: Source;
};
export type FindingStatus = "active" | "cleared" | "superseded";
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
 * Key of reviewed safety guidance text held in the knowledge base. Required for urgent findings.
 */
export type SafetyGuidanceKey = "sg_circuit_overload" | "sg_repeated_trips" | "sg_burst_pipe";
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "Source".
 */
export type Source = {
  detector: string;
  kind: SourceKind;
  model?: string;
  version: string;
  /**
   * Required for trained models (PRD §15.1).
   */
  model_card?: string;
};
/**
 * Reviewed safety guidance an urgent finding points to. The knowledge base (M6) holds the reviewed text for every key; the agent and apps show that text and never write their own. sg_circuit_overload: a circuit draws more than 1.45x its MCB rating. sg_repeated_trips: the same protective device tripped repeatedly. sg_burst_pipe: continuous high water flow (shut the main stop valve).
 *
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "SafetyGuidanceKey".
 */
export type SafetyGuidanceKey1 = "sg_circuit_overload" | "sg_repeated_trips" | "sg_burst_pipe";
/**
 * Stable, unique, human-readable ID of a physical element: <kind-prefix>_<descriptor>, lowercase snake_case. The prefix must match the element kind (enforced by the semantic validator).
 */
export type ObjectId1 = string;
/**
 * Stable, unique, human-readable ID of a physical element: <kind-prefix>_<descriptor>, lowercase snake_case. The prefix must match the element kind (enforced by the semantic validator).
 */
export type ObjectId2 = string;
/**
 * POST /detect/v1/checks.
 *
 * This interface was referenced by `DetectApi`'s JSON-Schema
 * via the `definition` "CheckRequest".
 */
export type CheckRequest = FlowStopCheck | TankLevelTrendCheck | CircuitLoadCheck | ResidualDropCheck;
/**
 * pending: house time has not yet reached the end of the measurement window. complete: measured. unavailable: the readings needed are missing (sensor absent or too little data).
 *
 * This interface was referenced by `DetectApi`'s JSON-Schema
 * via the `definition` "CheckStatus".
 */
export type CheckStatus = "pending" | "complete" | "unavailable";
/**
 * flow_stop: stopped (below expect_below_lpm), reduced (significantly lower, not below), unchanged. tank_level_trend: falling, steady, rising. circuit_load: dropped, unchanged, rose. Significance: the difference exceeds twice its standard error and the stream's resolution.
 *
 * This interface was referenced by `DetectApi`'s JSON-Schema
 * via the `definition` "CheckVerdict".
 */
export type CheckVerdict = "stopped" | "reduced" | "unchanged" | "falling" | "steady" | "rising" | "dropped" | "rose";
/**
 * Instant in house time (see M4): an RFC 3339 date-time, which always carries an explicit UTC offset (validators must assert the format). Services emit +08:00 (Asia/Kuala_Lumpur).
 */
export type HouseTime1 = string;
/**
 * degraded: not connected to M4, or some streams stalled.
 */
export type HealthStatus = "ok" | "degraded";
/**
 * Every detector has seen all readings up to here.
 */
export type NullableHouseTime = string | null;

/**
 * Payloads of the M5 detection service /detect/v1 (docs/design/interfaces.md): detectors, layer consent, on-demand runs, guided checks, localisation and health. Findings themselves are Finding documents (finding.schema.json).
 */
export interface DetectApi {
  [k: string]: unknown;
}
/**
 * A deployed detector and whether its layer is enabled.
 *
 * This interface was referenced by `DetectApi`'s JSON-Schema
 * via the `definition` "DetectorStatus".
 */
export interface DetectorStatus {
  name: string;
  layer: Layer;
  kind: SourceKind;
  version: string;
  model?: string;
  model_card?: string;
  /**
   * False while the household has switched the detector's layer off.
   */
  enabled: boolean;
}
/**
 * Layer consent (PRD §7.3). A disabled layer is not processed at all; re-enabling it starts its detectors afresh from that house time (baselines are re-commissioned).
 *
 * This interface was referenced by `DetectApi`'s JSON-Schema
 * via the `definition` "LayerState".
 */
export interface LayerState {
  layer: Layer;
  enabled: boolean;
}
/**
 * PUT /detect/v1/layers/{layer}.
 *
 * This interface was referenced by `DetectApi`'s JSON-Schema
 * via the `definition` "LayerUpdate".
 */
export interface LayerUpdate {
  enabled: boolean;
}
/**
 * POST /detect/v1/run: run one detector afresh over stored readings (the agent's run_detector). Nothing is stored or published. Detectors that need a commissioning period find nothing in a shorter window.
 *
 * This interface was referenced by `DetectApi`'s JSON-Schema
 * via the `definition` "RunRequest".
 */
export interface RunRequest {
  detector: string;
  from: HouseTime;
  to: HouseTime;
}
/**
 * This interface was referenced by `DetectApi`'s JSON-Schema
 * via the `definition` "RunResponse".
 */
export interface RunResponse {
  detector: string;
  from: HouseTime;
  to: HouseTime;
  /**
   * The latest revision of each finding the run produced.
   */
  findings: Finding[];
}
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
/**
 * Did the metered flow fall after the household's action at `since`? Compares the window before `since` with the window starting `settle_s` after it.
 *
 * This interface was referenced by `DetectApi`'s JSON-Schema
 * via the `definition` "FlowStopCheck".
 */
export interface FlowStopCheck {
  kind: "flow_stop";
  since: HouseTime;
  settle_s: number;
  window_s: number;
  expect_below_lpm: number;
  valve_id?: ObjectId1;
}
/**
 * Is the roof tank level still falling over the window starting at `since`?
 *
 * This interface was referenced by `DetectApi`'s JSON-Schema
 * via the `definition` "TankLevelTrendCheck".
 */
export interface TankLevelTrendCheck {
  kind: "tank_level_trend";
  since: HouseTime;
  window_s: number;
  valve_id?: ObjectId2;
}
/**
 * What does the circuit draw after the household's action at `since` (e.g. unplugging an appliance), compared with before?
 *
 * This interface was referenced by `DetectApi`'s JSON-Schema
 * via the `definition` "CircuitLoadCheck".
 */
export interface CircuitLoadCheck {
  kind: "circuit_load";
  circuit_id: ObjectId;
  since: HouseTime;
  settle_s: number;
  window_s: number;
}
/**
 * (contract 1.5.0) Did the residual (earth leakage) current through an RCCB fall when the household switched off one circuit's MCB at `since`? Compares the window before `since` with the window starting `settle_s` after it, and confirms from the circuit's current that it was off. Verdict: dropped, unchanged or rose.
 *
 * This interface was referenced by `DetectApi`'s JSON-Schema
 * via the `definition` "ResidualDropCheck".
 */
export interface ResidualDropCheck {
  kind: "residual_drop";
  rccb_id: ObjectId;
  circuit_id: ObjectId;
  since: HouseTime;
  settle_s: number;
  window_s: number;
}
/**
 * GET /detect/v1/checks/{id}.
 *
 * This interface was referenced by `DetectApi`'s JSON-Schema
 * via the `definition` "Check".
 */
export interface Check {
  check_id: string;
  request: CheckRequest;
  status: CheckStatus;
  ready_at: HouseTime1;
  measurements: EvidenceItem[];
  /**
   * Null until complete.
   */
  verdict: CheckVerdict | null;
  /**
   * Why a check is unavailable.
   */
  reason?: string;
}
/**
 * GET /detect/v1/health (unauthenticated).
 *
 * This interface was referenced by `DetectApi`'s JSON-Schema
 * via the `definition` "DetectHealth".
 */
export interface DetectHealth {
  status: HealthStatus;
  /**
   * Subscribed to M4's lossless stream.
   */
  connected: boolean;
  episode: number | null;
  processed_until: NullableHouseTime;
  /**
   * House seconds between M4's clock and processed_until.
   */
  lag_s: number | null;
  /**
   * Samples that arrived for house time already processed (ignored).
   */
  late_samples: number;
  /**
   * Streams the detectors read that stopped arriving; processing continues without them.
   */
  stalled_streams: StreamId[];
  detectors: DetectorVersion[];
}
export interface DetectorVersion {
  name: string;
  version: string;
  model?: string;
}
/**
 * (contract 1.5.0) POST /detect/v1/localise: where is the problem a finding describes, given the guided checks done for it so far, and which check should come next? Supported for L1 water leaks (valve checks) and L2 earth leakage (residual_drop checks).
 *
 * This interface was referenced by `DetectApi`'s JSON-Schema
 * via the `definition` "LocaliseRequest".
 */
export interface LocaliseRequest {
  finding_id: string;
  /**
   * Completed checks for this finding, in the order they were done.
   *
   * @maxItems 20
   */
  check_ids: string[];
}
/**
 * This interface was referenced by `DetectApi`'s JSON-Schema
 * via the `definition` "LocalisedCandidate".
 */
export interface LocalisedCandidate {
  object_id: ObjectId;
  p: number;
}
/**
 * The most informative next check. The agent turns it into a request (adding `since`) once the household has done the action.
 *
 * This interface was referenced by `DetectApi`'s JSON-Schema
 * via the `definition` "CheckSuggestion".
 */
export interface CheckSuggestion {
  kind: "flow_stop" | "tank_level_trend" | "residual_drop";
  /**
   * What the household is asked to do (and to undo after the window).
   */
  action: "close_valve" | "switch_off_circuit";
  valve_id?: ObjectId;
  circuit_id?: ObjectId;
  rccb_id?: ObjectId;
  expect_below_lpm?: number;
  settle_s: number;
  window_s: number;
  expected_gain_bits: number;
  /**
   * Ask the household not to use water from the window before the action until the window ends.
   */
  hold_water_use?: boolean;
  /**
   * Flow checks read a roof-tank refill as flow; do them once the tank level is back at full.
   */
  wait_for_full_tank?: boolean;
}
/**
 * (contract 1.5.0) Location probabilities after the checks, most likely first, and the next check (null when the localiser is confident enough, out of useful checks, or at its check limit). Show them as possibilities, not facts.
 *
 * This interface was referenced by `DetectApi`'s JSON-Schema
 * via the `definition` "LocaliseResponse".
 */
export interface LocaliseResponse {
  finding_id: string;
  layer: "L1" | "L2";
  /**
   * @maxItems 10
   */
  candidates: LocalisedCandidate[];
  /**
   * Probability of every place not listed.
   */
  other_p: number;
  entropy_bits: number;
  checks_used: {
    check_id: string;
    /**
     * The verdict as the localiser used it: a check's verdict, idle (no flow before the action: no evidence), unavailable, or ignored (the check does not name its valve or circuit, or is not complete).
     */
    evidence: string;
  }[];
  next_check: CheckSuggestion | null;
}
