/* GENERATED from packages/contract/src/penjaga_contract/schemas by packages/contract/tools/generate.py. Do not edit by hand. */

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
export type Progression = Progression1;
export type ProgressionKind = "constant" | "intermittent" | "worsening";
/**
 * House time to start; null means now.
 */
export type NullableHouseTime = string | null;

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
export interface Progression1 {
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
}
