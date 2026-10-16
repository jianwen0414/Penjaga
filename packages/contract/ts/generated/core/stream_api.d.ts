/* GENERATED from packages/contract/src/penjaga_contract/schemas by packages/contract/tools/generate.py. Do not edit by hand. */

/**
 * Instant in house time (see M4): an RFC 3339 date-time, which always carries an explicit UTC offset (validators must assert the format). Services emit +08:00 (Asia/Kuala_Lumpur).
 */
export type HouseTime = string;
/**
 * Concrete stream ID from the stream catalogue (streams.json), dot-separated, e.g. elec.circuit.circ_ac_master.current_a.
 */
export type StreamId = string;
/**
 * Per-sample quality flag. ok: valid measurement. missing: no value (value must be null). estimated: value filled or derived by the sensor/gateway. out_of_range: outside the stream's physical range (value kept as reported). stale: value repeated from an earlier sample because the sensor did not update.
 */
export type Quality = "ok" | "missing" | "estimated" | "out_of_range" | "stale";
export type StreamStatusKind = "sampled" | "event";
/**
 * Timestamp of the latest sample or event received in this episode.
 */
export type NullableHouseTime = string | null;
/**
 * ID of one house instance.
 */
export type HouseId = string;
/**
 * @minItems 1
 * @maxItems 100000
 */
export type QualityPerSample = [Quality, ...Quality[]];
/**
 * Stable, unique, human-readable ID of a physical element: <kind-prefix>_<descriptor>, lowercase snake_case. The prefix must match the element kind (enforced by the semantic validator).
 */
export type ObjectId = string;
export type ProtectionState = "tripped" | "reset";
export type MainsState = "lost" | "restored";
export type ClockMode = "real" | "accelerated";
/**
 * Instant in house time (see M4): an RFC 3339 date-time, which always carries an explicit UTC offset (validators must assert the format). Services emit +08:00 (Asia/Kuala_Lumpur).
 */
export type HouseTime1 = string;
/**
 * What a producer (simulator or device gateway) sends to /stream/v1/ingest.
 *
 * This interface was referenced by `StreamApi`'s JSON-Schema
 * via the `definition` "IngestMessage".
 */
export type IngestMessage = BatchMessage | EventMessage;
/**
 * What /stream/v1/subscribe (lossless, detector role) and /stream/v1/live (aggregated, app/operator) send.
 *
 * This interface was referenced by `StreamApi`'s JSON-Schema
 * via the `definition` "SubscriberMessage".
 */
export type SubscriberMessage =
  BatchMessage | EventMessage | ClockMessage | FrameMessage | ResetMessage | RejectedMessage;

/**
 * Payloads of the M4 stream service API (docs/design/interfaces.md): summaries, raw samples, stream status, live frames and the WebSocket message envelope.
 */
export interface StreamApi {
  [k: string]: unknown;
}
/**
 * Aggregate of one bucket [t, t + bucket_s).
 *
 * This interface was referenced by `StreamApi`'s JSON-Schema
 * via the `definition` "SummaryPoint".
 */
export interface SummaryPoint {
  t: HouseTime;
  /**
   * Number of non-missing samples in the bucket.
   */
  n: number;
  min: number | null;
  max: number | null;
  mean: number | null;
  last: number | null;
}
/**
 * Bucketed summary of one stream, at most 500 points (data minimisation, PRD §7.5). Empty buckets are omitted.
 *
 * This interface was referenced by `StreamApi`'s JSON-Schema
 * via the `definition` "SummaryResponse".
 */
export interface SummaryResponse {
  stream: StreamId;
  unit: string;
  from: HouseTime;
  to: HouseTime;
  bucket_s: number;
  /**
   * @maxItems 500
   */
  points: SummaryPoint[];
}
/**
 * This interface was referenced by `StreamApi`'s JSON-Schema
 * via the `definition` "RawSample".
 */
export interface RawSample {
  t: HouseTime;
  value: number | null;
  quality: Quality;
}
/**
 * Raw samples of one stream (operator and detector roles only), at most 100,000 samples.
 *
 * This interface was referenced by `StreamApi`'s JSON-Schema
 * via the `definition` "RawResponse".
 */
export interface RawResponse {
  stream: StreamId;
  unit: string;
  from: HouseTime;
  to: HouseTime;
  /**
   * @maxItems 100000
   */
  samples: RawSample[];
}
/**
 * This interface was referenced by `StreamApi`'s JSON-Schema
 * via the `definition` "StreamStatus".
 */
export interface StreamStatus {
  stream: StreamId;
  kind: StreamStatusKind;
  last_at: NullableHouseTime;
  /**
   * Samples (or events) received in this episode.
   */
  samples: number;
  /**
   * Discontinuities detected (skipped batch numbers or time gaps).
   */
  gaps: number;
  last_seq: number | null;
}
/**
 * Aggregate of the samples of one stream received since the previous live frame.
 *
 * This interface was referenced by `StreamApi`'s JSON-Schema
 * via the `definition` "LiveStreamAggregate".
 */
export interface LiveStreamAggregate {
  t_last: HouseTime;
  n: number;
  min: number | null;
  max: number | null;
  mean: number | null;
  last: number | null;
}
/**
 * Periodic aggregated update for UI subscribers. Events are never aggregated: they are delivered as separate 'event' messages.
 *
 * This interface was referenced by `StreamApi`'s JSON-Schema
 * via the `definition` "LiveFrame".
 */
export interface LiveFrame {
  house_time: HouseTime;
  /**
   * Keyed by stream id; only streams with new samples.
   */
  streams: {
    [k: string]: LiveStreamAggregate;
  };
}
/**
 * Why an ingested message was refused (codes from penjaga_contract.validate).
 *
 * This interface was referenced by `StreamApi`'s JSON-Schema
 * via the `definition` "Rejection".
 */
export interface Rejection {
  stream?: StreamId;
  seq?: number;
  /**
   * @minItems 1
   */
  codes: [string, ...string[]];
  message: string;
}
/**
 * This interface was referenced by `StreamApi`'s JSON-Schema
 * via the `definition` "ResetNotice".
 */
export interface ResetNotice {
  episode: number;
  start: HouseTime;
}
/**
 * This interface was referenced by `StreamApi`'s JSON-Schema
 * via the `definition` "BatchMessage".
 */
export interface BatchMessage {
  type: "batch";
  data: ReadingBatch;
}
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
/**
 * This interface was referenced by `StreamApi`'s JSON-Schema
 * via the `definition` "EventMessage".
 */
export interface EventMessage {
  type: "event";
  data: Event;
}
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
/**
 * This interface was referenced by `StreamApi`'s JSON-Schema
 * via the `definition` "ClockMessage".
 */
export interface ClockMessage {
  type: "clock";
  data: ClockState;
}
export interface ClockState {
  house_time: HouseTime;
  mode: ClockMode;
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
 * This interface was referenced by `StreamApi`'s JSON-Schema
 * via the `definition` "FrameMessage".
 */
export interface FrameMessage {
  type: "frame";
  data: LiveFrame;
}
/**
 * This interface was referenced by `StreamApi`'s JSON-Schema
 * via the `definition` "ResetMessage".
 */
export interface ResetMessage {
  type: "reset";
  data: ResetNotice;
}
/**
 * This interface was referenced by `StreamApi`'s JSON-Schema
 * via the `definition` "RejectedMessage".
 */
export interface RejectedMessage {
  type: "rejected";
  data: Rejection;
}
