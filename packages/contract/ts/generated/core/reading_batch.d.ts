/* GENERATED from packages/contract/src/penjaga_contract/schemas by packages/contract/tools/generate.py. Do not edit by hand. */

/**
 * ID of one house instance.
 */
export type HouseId = string;
/**
 * Concrete stream ID from the stream catalogue (streams.json), dot-separated, e.g. elec.circuit.circ_ac_master.current_a.
 */
export type StreamId = string;
/**
 * Instant in house time (see M4): an RFC 3339 date-time, which always carries an explicit UTC offset (validators must assert the format). Services emit +08:00 (Asia/Kuala_Lumpur).
 */
export type HouseTime = string;
/**
 * Per-sample quality flag. ok: valid measurement. missing: no value (value must be null). estimated: value filled or derived by the sensor/gateway. out_of_range: outside the stream's physical range (value kept as reported). stale: value repeated from an earlier sample because the sensor did not update.
 */
export type Quality = "ok" | "missing" | "estimated" | "out_of_range" | "stale";
/**
 * @minItems 1
 * @maxItems 100000
 */
export type QualityPerSample = [Quality, ...Quality[]];

/**
 * A run of regularly spaced samples from one sampled stream: sample i has timestamp t0 + i * period_s. Internal transport and storage format. Carries only what a real meter reports: there is deliberately no metadata field. Semantic rules (checked by penjaga_contract.validate): quality array length equals values length; a sample is null exactly when its quality is 'missing'; seq increases by one per batch per stream.
 */
export interface ReadingBatch {
  house_id: HouseId;
  stream: StreamId;
  t0: HouseTime;
  period_s: number;
  unit: string;
  /**
   * @minItems 1
   * @maxItems 100000
   */
  values: [number | null, ...(number | null)[]];
  /**
   * A single flag applying to every sample, or one flag per sample.
   */
  quality: Quality | QualityPerSample;
  /**
   * Per-stream batch sequence number, used to detect gaps and measure latency.
   */
  seq: number;
}
