/* GENERATED from packages/contract/src/penjaga_contract/schemas by packages/contract/tools/generate.py. Do not edit by hand. */

export type Split = "train" | "validation" | "test";
/**
 * ID of one house instance.
 */
export type HouseId = string;
/**
 * Instant in house time (see M4): an RFC 3339 date-time, which always carries an explicit UTC offset (validators must assert the format). Services emit +08:00 (Asia/Kuala_Lumpur).
 */
export type HouseTime = string;
/**
 * Stable, unique, human-readable ID of a physical element: <kind-prefix>_<descriptor>, lowercase snake_case. The prefix must match the element kind (enforced by the semantic validator).
 */
export type ObjectId = string;
/**
 * W1 dripping tap, W2 running/leaking WC cistern, W3 roof-tank float-valve fault (overflow), W4 concealed pipe leak, W5 burst pipe, E1 sustained circuit overload, E2 rising earth leakage, E3 repeated protective-device trips, A1 aircon filter clogging, A2 aircon low refrigerant, A3 fridge door-seal failure, A4 fridge compressor wear (rising running power, short-cycling), U1 scheduled water disruption, U2 unannounced supply drop, U3 planned power outage.
 *
 * This interface was referenced by `FaultSpec`'s JSON-Schema
 * via the `definition` "FaultType".
 */
export type FaultType =
  "W1" | "W2" | "W3" | "W4" | "W5" | "E1" | "E2" | "E3" | "A1" | "A2" | "A3" | "A4" | "U1" | "U2" | "U3";
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
export type NullableHouseTime = string | null;
/**
 * Legitimate behaviour that must not raise false alarms (PRD §12.3, extended in PRD v1.1).
 */
export type AnomalyType =
  | "night_bucket_fill"
  | "early_morning_routine"
  | "washer_delay_timer"
  | "guests"
  | "heatwave"
  | "holiday_absence"
  | "festive_cooking"
  | "aircon_setpoint_change"
  | "occupancy_change"
  | "post_outage_tank_refill";

/**
 * LAB NAMESPACE: one evaluation/training episode for M11. Everything needed to regenerate the episode deterministically. Test-split manifests are sealed (hash recorded) and every test run is logged.
 */
export interface EpisodeManifest {
  schema_version: "1.0";
  episode_id: string;
  split: Split;
  /**
   * Bounded to 2^53 - 1 so it survives JSON round-trips in JavaScript.
   */
  seed: number;
  house_id: HouseId;
  household_archetype: string;
  start: HouseTime;
  duration_days: number;
  /**
   * Sensor-kit configuration (enables with/without ablations, e.g. no tank level sensor).
   *
   * @minItems 1
   */
  enabled_sensor_ids: [ObjectId, ...ObjectId[]];
  faults: FaultSpec[];
  anomalies: AnomalySpec[];
  generator_version: string;
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
  start_at?: NullableHouseTime;
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
 * This interface was referenced by `EpisodeManifest`'s JSON-Schema
 * via the `definition` "AnomalySpec".
 */
export interface AnomalySpec {
  anomaly_type: AnomalyType;
  start_at: HouseTime;
  duration_s: number;
  params?: {
    [k: string]: unknown;
  };
}
