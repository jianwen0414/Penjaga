/* GENERATED from packages/contract/src/penjaga_contract/schemas by packages/contract/tools/generate.py. Do not edit by hand. */

/**
 * Stable, unique, human-readable ID of a physical element: <kind-prefix>_<descriptor>, lowercase snake_case. The prefix must match the element kind (enforced by the semantic validator).
 *
 * This interface was referenced by `Common`'s JSON-Schema
 * via the `definition` "ObjectId".
 */
export type ObjectId = string;
/**
 * An ObjectId or null.
 *
 * This interface was referenced by `Common`'s JSON-Schema
 * via the `definition` "NullableObjectId".
 */
export type NullableObjectId = string | null;
/**
 * ID of one house instance.
 *
 * This interface was referenced by `Common`'s JSON-Schema
 * via the `definition` "HouseId".
 */
export type HouseId = string;
/**
 * Floor level index: 0 ground floor, increasing upwards.
 *
 * This interface was referenced by `Common`'s JSON-Schema
 * via the `definition` "LevelIndex".
 */
export type LevelIndex = number;
/**
 * Concrete stream ID from the stream catalogue (streams.json), dot-separated, e.g. elec.circuit.circ_ac_master.current_a.
 *
 * This interface was referenced by `Common`'s JSON-Schema
 * via the `definition` "StreamId".
 */
export type StreamId = string;
/**
 * Instant in house time (see M4): an RFC 3339 date-time, which always carries an explicit UTC offset (validators must assert the format). Services emit +08:00 (Asia/Kuala_Lumpur).
 *
 * This interface was referenced by `Common`'s JSON-Schema
 * via the `definition` "HouseTime".
 */
export type HouseTime = string;
/**
 * A HouseTime or null.
 *
 * This interface was referenced by `Common`'s JSON-Schema
 * via the `definition` "NullableHouseTime".
 */
export type NullableHouseTime = string | null;
/**
 * Calendar date (RFC 3339 full-date, YYYY-MM-DD) in the house's local time zone.
 *
 * This interface was referenced by `Common`'s JSON-Schema
 * via the `definition` "CalendarDate".
 */
export type CalendarDate = string;
/**
 * Preventive layer: L1 leaks, L2 electrical safety, L3 appliance health, L4 maintenance, L5 management integration.
 *
 * This interface was referenced by `Common`'s JSON-Schema
 * via the `definition` "Layer".
 */
export type Layer = "L1" | "L2" | "L3" | "L4" | "L5";
/**
 * Ordered severity scale. 'urgent' is reserved for safety (e.g. electrical hazard, burst pipe).
 *
 * This interface was referenced by `Common`'s JSON-Schema
 * via the `definition` "Severity".
 */
export type Severity = "info" | "heads_up" | "act_soon" | "urgent";
/**
 * Per-sample quality flag. ok: valid measurement. missing: no value (value must be null). estimated: value filled or derived by the sensor/gateway. out_of_range: outside the stream's physical range (value kept as reported). stale: value repeated from an earlier sample because the sensor did not update.
 *
 * This interface was referenced by `Common`'s JSON-Schema
 * via the `definition` "Quality".
 */
export type Quality = "ok" | "missing" | "estimated" | "out_of_range" | "stale";
/**
 * design_target: a specification we require of the sensor we would buy. datasheet: taken from a specific product's datasheet.
 *
 * This interface was referenced by `Common`'s JSON-Schema
 * via the `definition` "SpecBasis".
 */
export type SpecBasis = "design_target" | "datasheet";
/**
 * Reference to an entry in docs/DATA_SOURCES.md (e.g. DS-007).
 *
 * This interface was referenced by `Common`'s JSON-Schema
 * via the `definition` "SourceRef".
 */
export type SourceRef = string;

/**
 * Shared definitions for the Penjaga House Data Contract (M0). Referenced by every other contract schema.
 */
export interface Common {
  [k: string]: unknown;
}
/**
 * Half-open house-time interval [from, to).
 *
 * This interface was referenced by `Common`'s JSON-Schema
 * via the `definition` "TimeWindow".
 */
export interface TimeWindow {
  from: HouseTime;
  to: HouseTime;
}
/**
 * A physical quantity with unit and optional 90% interval. Monetary values are never computed by detection; the M6 tariff calculator converts physical quantities to RM.
 *
 * This interface was referenced by `Common`'s JSON-Schema
 * via the `definition` "Quantity".
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
