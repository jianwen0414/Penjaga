/* GENERATED from packages/contract/src/penjaga_contract/schemas by packages/contract/tools/generate.py. Do not edit by hand. */

/**
 * LAB NAMESPACE: ground truth written by the simulator to the truth store only (PRD §12.1). Read by the M11 scorer and training pipelines, never by detection at inference time.
 */
export type TruthRecord = {
  record_id: string;
  episode_id?: string;
  house_id: HouseId;
  kind: TruthKind;
  /**
   * Fault type (W1..U3) or legitimate-anomaly type.
   */
  label: FaultType | AnomalyType;
  fault_id?: string;
  /**
   * True location(s).
   */
  object_ids: ObjectId[];
  start: HouseTime;
  end: NullableHouseTime;
  /**
   * Realised parameters (e.g. actual flow L/min, refill period, growth rate), for magnitude-stratified evaluation.
   */
  params: {
    [k: string]: unknown;
  };
};
/**
 * ID of one house instance.
 */
export type HouseId = string;
export type TruthKind = "fault" | "legit_anomaly";
/**
 * W1 dripping tap, W2 running/leaking WC cistern, W3 roof-tank float-valve fault (overflow), W4 concealed pipe leak, W5 burst pipe, E1 sustained circuit overload, E2 rising earth leakage, E3 repeated protective-device trips, A1 aircon filter clogging, A2 aircon low refrigerant, A3 fridge door-seal failure, A4 fridge compressor wear (rising running power, short-cycling), U1 scheduled water disruption, U2 unannounced supply drop, U3 planned power outage.
 */
export type FaultType =
  "W1" | "W2" | "W3" | "W4" | "W5" | "E1" | "E2" | "E3" | "A1" | "A2" | "A3" | "A4" | "U1" | "U2" | "U3";
/**
 * Legitimate behaviour that must not raise false alarms (PRD §12.3, extended in PRD v1.1). low_supply_pressure (contract 1.4.0): the network pressure is low during the day and recovers at night, so the roof tank refills at night.
 *
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "AnomalyType".
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
  | "post_outage_tank_refill"
  | "low_supply_pressure";
/**
 * Stable, unique, human-readable ID of a physical element: <kind-prefix>_<descriptor>, lowercase snake_case. The prefix must match the element kind (enforced by the semantic validator).
 */
export type ObjectId = string;
/**
 * Instant in house time (see M4): an RFC 3339 date-time, which always carries an explicit UTC offset (validators must assert the format). Services emit +08:00 (Asia/Kuala_Lumpur).
 */
export type HouseTime = string;
/**
 * Null while ongoing.
 */
export type NullableHouseTime = string | null;
