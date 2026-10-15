/* GENERATED from packages/contract/src/penjaga_contract/schemas by packages/contract/tools/generate.py. Do not edit by hand. */

/**
 * One sample from one sampled stream (PRD §10.4). Used at API edges and in single-value realtime messages. Carries only what a real meter reports: there is deliberately no metadata field.
 */
export type Reading = {
  ts: HouseTime;
  house_id: HouseId;
  stream: StreamId;
  value: number | null;
  unit: string;
  quality: Quality;
};
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
 * Per-sample quality flag. ok: valid measurement. missing: no value (value must be null). estimated: value filled or derived by the sensor/gateway. out_of_range: outside the stream's physical range (value kept as reported). stale: value repeated from an earlier sample because the sensor did not update.
 */
export type Quality = "ok" | "missing" | "estimated" | "out_of_range" | "stale";
