/* GENERATED from packages/contract/src/penjaga_contract/schemas by packages/contract/tools/generate.py. Do not edit by hand. */

/**
 * Instant in house time (see M4): an RFC 3339 date-time, which always carries an explicit UTC offset (validators must assert the format). Services emit +08:00 (Asia/Kuala_Lumpur).
 */
export type HouseTime = string;
/**
 * ID of one house instance.
 */
export type HouseId = string;
/**
 * Concrete stream ID from the stream catalogue (streams.json), dot-separated, e.g. elec.circuit.circ_ac_master.current_a.
 */
export type StreamId = string;
/**
 * Stable, unique, human-readable ID of a physical element: <kind-prefix>_<descriptor>, lowercase snake_case. The prefix must match the element kind (enforced by the semantic validator).
 */
export type ObjectId = string;
export type ProtectionState = "tripped" | "reset";
export type MainsState = "lost" | "restored";

/**
 * A discrete event from an event stream. Events are never aggregated or dropped (PRD §14). Payloads carry only what the physical device reports: auxiliary contacts report tripped/reset state, not the cause.
 */
export interface Event {
  ts: HouseTime;
  house_id: HouseId;
  stream: StreamId;
  seq: number;
  payload: ProtectionDeviceState | SupplyState;
}
/**
 * State change of a protective device (stream elec.protection.trip).
 *
 * This interface was referenced by `Event`'s JSON-Schema
 * via the `definition` "ProtectionDeviceState".
 */
export interface ProtectionDeviceState {
  type: "protection_device_state";
  device_id: ObjectId;
  state: ProtectionState;
}
/**
 * Mains presence at the incomer (stream elec.supply.state).
 *
 * This interface was referenced by `Event`'s JSON-Schema
 * via the `definition` "SupplyState".
 */
export interface SupplyState {
  type: "supply_state";
  state: MainsState;
}
