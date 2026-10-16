/* GENERATED from packages/contract/src/penjaga_contract/schemas by packages/contract/tools/generate.py. Do not edit by hand. */

/**
 * The M4 clock episode the simulator is publishing; null before the first world reset.
 *
 * This interface was referenced by `SimApi`'s JSON-Schema
 * via the `definition` "NullableEpisode".
 */
export type NullableEpisode = number | null;
/**
 * Stable, unique, human-readable ID of a physical element: <kind-prefix>_<descriptor>, lowercase snake_case. The prefix must match the element kind (enforced by the semantic validator).
 */
export type ObjectId = string;
/**
 * House time up to which readings have been sent (rounded up to the second).
 */
export type NullableHouseTime = string | null;
/**
 * Instant in house time (see M4): an RFC 3339 date-time, which always carries an explicit UTC offset (validators must assert the format). Services emit +08:00 (Asia/Kuala_Lumpur).
 */
export type HouseTime = string;
/**
 * Clock mode; omitted or null means accelerated.
 */
export type ClockMode = "real" | "accelerated";
export type ClockMode1 = "real" | "accelerated";
/**
 * Instant in house time (see M4): an RFC 3339 date-time, which always carries an explicit UTC offset (validators must assert the format). Services emit +08:00 (Asia/Kuala_Lumpur).
 */
export type HouseTime1 = string;
/**
 * W1 dripping tap, W2 running/leaking WC cistern, W3 roof-tank float-valve fault (overflow), W4 concealed pipe leak, W5 burst pipe, E1 sustained circuit overload, E2 rising earth leakage, E3 repeated protective-device trips, A1 aircon filter clogging, A2 aircon low refrigerant, A3 fridge door-seal failure, A4 fridge compressor wear (rising running power, short-cycling), U1 scheduled water disruption, U2 unannounced supply drop, U3 planned power outage.
 *
 * This interface was referenced by `FaultSpec`'s JSON-Schema
 * via the `definition` "FaultType".
 */
export type FaultType =
  "W1" | "W2" | "W3" | "W4" | "W5" | "E1" | "E2" | "E3" | "A1" | "A2" | "A3" | "A4" | "U1" | "U2" | "U3";
/**
 * Null means now.
 */
export type NullableHouseTime1 = string | null;
/**
 * Relative to published house time.
 *
 * This interface was referenced by `SimApi`'s JSON-Schema
 * via the `definition` "FaultState".
 */
export type FaultState = "scheduled" | "active" | "ended";
/**
 * Contract element where the fault occurs; null for supply-level faults (U1-U3). Valid kinds per fault type are published by GET /sim/v1/faults/catalogue.
 */
export type NullableObjectId = string | null;
/**
 * This interface was referenced by `FaultSpec`'s JSON-Schema
 * via the `definition` "Progression".
 */
export type Progression = {
  kind: ProgressionKind;
  /**
   * intermittent: fraction of time the fault is active.
   */
  on_fraction?: number;
  /**
   * intermittent: mean on+off cycle length.
   */
  cycle_s?: number;
  /**
   * worsening: multiplicative growth of magnitude per day (e.g. 0.05 = +5%/day).
   */
  growth_per_day?: number;
};
export type ProgressionKind = "constant" | "intermittent" | "worsening";
/**
 * House time to start; null means now.
 */
export type NullableHouseTime2 = string | null;
/**
 * Null while the fault runs until cleared.
 */
export type NullableHouseTime3 = string | null;
/**
 * Preventive layer: L1 leaks, L2 electrical safety, L3 appliance health, L4 maintenance, L5 management integration.
 */
export type Layer = "L1" | "L2" | "L3" | "L4" | "L5";

/**
 * LAB NAMESPACE: request and response bodies of the M2 simulator control plane /sim/v1 (operator role only). Fault and anomaly requests use FaultSpec and AnomalySpec; ground truth is returned as TruthRecord. Never visible to detection.
 */
export interface SimApi {
  [k: string]: unknown;
}
/**
 * GET /sim/v1/health (unauthenticated: nothing here helps reconstruct the world).
 *
 * This interface was referenced by `SimApi`'s JSON-Schema
 * via the `definition` "SimHealth".
 */
export interface SimHealth {
  status: "ok";
  episode: NullableEpisode;
  generator: string;
}
/**
 * GET /sim/v1/status.
 *
 * This interface was referenced by `SimApi`'s JSON-Schema
 * via the `definition` "SimStatus".
 */
export interface SimStatus {
  episode: NullableEpisode;
  seed: number | null;
  archetype: string | null;
  /**
   * Sensor-kit ablation in force; null means the full kit.
   */
  enabled_sensors: ObjectId[] | null;
  published_until: NullableHouseTime;
  messages_sent: number;
  /**
   * Faults in the current world (any state).
   */
  faults: number;
  last_error: string | null;
}
/**
 * POST /sim/v1/world/reset: M4's clock and store are reset and a fresh world begins.
 *
 * This interface was referenced by `SimApi`'s JSON-Schema
 * via the `definition` "ResetRequest".
 */
export interface ResetRequest {
  start: HouseTime;
  mode?: ClockMode;
  /**
   * House seconds per wall second in accelerated mode; omitted or null means 60.
   */
  speed?: number | null;
  /**
   * Null draws a fresh seed.
   */
  seed?: number | null;
  /**
   * Household archetype; null draws one.
   */
  archetype?: string | null;
  /**
   * Sensor-kit ablation; null for the full kit.
   *
   * @minItems 1
   */
  enabled_sensor_ids?: [ObjectId, ...ObjectId[]] | null;
}
/**
 * This interface was referenced by `SimApi`'s JSON-Schema
 * via the `definition` "ResetResponse".
 */
export interface ResetResponse {
  clock: ClockState;
  status: SimStatus;
}
export interface ClockState {
  house_time: HouseTime;
  mode: ClockMode1;
  /**
   * Requested house seconds per wall second (1 in real mode).
   */
  speed: number;
  /**
   * Speed actually applied. Lower than speed when the governor slows house time so detection keeps up (data is never dropped); 0 while paused.
   */
  effective_speed: number;
  paused: boolean;
  /**
   * Increments on every world reset. Readings from different episodes never mix.
   */
  episode: number;
  episode_start: HouseTime1;
}
/**
 * POST /sim/v1/faults/random: a plausible fault of this type at a random valid location and magnitude.
 *
 * This interface was referenced by `SimApi`'s JSON-Schema
 * via the `definition` "RandomFaultRequest".
 */
export interface RandomFaultRequest {
  fault_type: FaultType;
  start_at?: NullableHouseTime1;
}
/**
 * A fault as the operator sees it: the accepted spec, its schedule and state.
 *
 * This interface was referenced by `SimApi`'s JSON-Schema
 * via the `definition` "FaultView".
 */
export interface FaultView {
  fault_id: string;
  spec: FaultSpec;
  start: HouseTime;
  end: NullableHouseTime3;
  state: FaultState;
}
/**
 * LAB NAMESPACE: fault to inject into the simulator (PRD §12.2) via the M2 control-plane API. Never visible to detection (import rules forbid penjaga_detect from importing penjaga_contract.lab).
 */
export interface FaultSpec {
  /**
   * Assigned by the simulator when accepted.
   */
  fault_id?: string;
  fault_type: FaultType;
  location_id: NullableObjectId;
  magnitude: FaultMagnitude;
  progression: Progression;
  start_at?: NullableHouseTime2;
  /**
   * Null means until cleared.
   */
  duration_s?: number | null;
}
export interface FaultMagnitude {
  value: number;
  unit: string;
}
/**
 * GET /sim/v1/faults/catalogue: what the Lab may inject for one fault type.
 *
 * This interface was referenced by `SimApi`'s JSON-Schema
 * via the `definition` "CatalogueEntry".
 */
export interface CatalogueEntry {
  fault_type: FaultType;
  layer: Layer;
  magnitude_unit: string;
  /**
   * Calibrated [low, high] of the magnitude.
   *
   * @minItems 2
   * @maxItems 2
   */
  magnitude_range: never[];
  /**
   * Valid location_id values; empty for supply-level faults.
   */
  locations: ObjectId[];
  requires_duration: boolean;
}
/**
 * POST /sim/v1/anomalies.
 *
 * This interface was referenced by `SimApi`'s JSON-Schema
 * via the `definition` "AnomalyCreated".
 */
export interface AnomalyCreated {
  record_id: string;
}
