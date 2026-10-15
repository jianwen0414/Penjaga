/* GENERATED from packages/contract/src/penjaga_contract/schemas by packages/contract/tools/generate.py. Do not edit by hand. */

/**
 * ID of one house instance.
 */
export type HouseId = string;
/**
 * This interface was referenced by `StreamCatalogue`'s JSON-Schema
 * via the `definition` "StreamDef".
 */
export type StreamDef = StreamDef1;
/**
 * Concrete stream ID from the stream catalogue (streams.json), dot-separated, e.g. elec.circuit.circ_ac_master.current_a.
 */
export type StreamId = string;
/**
 * sampled: regular samples (ReadingBatch). event: irregular discrete events (Event), never aggregated or dropped.
 */
export type StreamKind = "sampled" | "event";
/**
 * How a sample relates to time. instant: value at the timestamp. mean_over_period / sum_over_period: over the period (ts - nominal_period_s, ts], i.e. the timestamp marks the END of the period.
 */
export type Aggregation = "instant" | "mean_over_period" | "sum_over_period" | "event";
export type Unit = "L/min" | "bar" | "%" | "W" | "V" | "A" | "Wh" | "mA" | "degC" | "mm" | "none";
/**
 * Preventive layer: L1 leaks, L2 electrical safety, L3 appliance health, L4 maintenance, L5 management integration.
 */
export type Layer = "L1" | "L2" | "L3" | "L4" | "L5";
/**
 * Stable, unique, human-readable ID of a physical element: <kind-prefix>_<descriptor>, lowercase snake_case. The prefix must match the element kind (enforced by the semantic validator).
 */
export type ObjectId = string;
/**
 * Payload type carried by an event stream (see event.schema.json).
 */
export type EventPayloadType = "protection_device_state" | "supply_state";

/**
 * Machine-readable stream catalogue (M0). It is the complete list of what detection may consume: no stream may exist that the real sensor kit could not provide. Generated from house.json by penjaga_contract.catalogue and checked for drift in tests.
 */
export interface StreamCatalogue {
  schema_version: "1.0";
  house_id: HouseId;
  /**
   * @minItems 1
   */
  streams: [StreamDef, ...StreamDef[]];
}
export interface StreamDef1 {
  id: StreamId;
  kind: StreamKind;
  aggregation: Aggregation;
  unit: Unit;
  /**
   * Sampling period in seconds; null for event streams.
   */
  nominal_period_s: number | null;
  value_min: number | null;
  value_max: number | null;
  /**
   * Smallest reported increment (quantisation step); null for event streams.
   */
  resolution: number | null;
  /**
   * Layers that may consume the stream. Layer consent gates processing per layer.
   *
   * @minItems 1
   */
  layers: [Layer, ...Layer[]];
  sensor_id: ObjectId;
  /**
   * Elements the stream measures (e.g. a circuit, an RCCB group, the meter).
   */
  object_ids: ObjectId[];
  event_payload?: EventPayloadType;
  description: string;
}
