/* GENERATED from packages/contract/src/penjaga_contract/schemas by packages/contract/tools/generate.py. Do not edit by hand. */

/**
 * This interface was referenced by `Clock`'s JSON-Schema
 * via the `definition` "ClockMode".
 */
export type ClockMode = "real" | "accelerated";
/**
 * Instant in house time (see M4): an RFC 3339 date-time, which always carries an explicit UTC offset (validators must assert the format). Services emit +08:00 (Asia/Kuala_Lumpur).
 */
export type HouseTime = string;
/**
 * Instant in house time (see M4): an RFC 3339 date-time, which always carries an explicit UTC offset (validators must assert the format). Services emit +08:00 (Asia/Kuala_Lumpur).
 */
export type HouseTime1 = string;

/**
 * House-time clock messages (M4). The backend owns one house-time clock; every service reads it. Real mode runs at wall time; accelerated mode is controlled by the operator (Lab).
 */
export interface Clock {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `Clock`'s JSON-Schema
 * via the `definition` "ClockState".
 */
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
  /**
   * Unique ID of the episode, new at every reset and every M4 start. Episode numbers restart from 0 when M4 restarts; this does not, so consumers key their state on it.
   */
  episode_id: string;
}
/**
 * Operator change to an accelerated clock. jump_to must not be earlier than the current house time (house time is monotonic; use a reset to start over).
 *
 * This interface was referenced by `Clock`'s JSON-Schema
 * via the `definition` "ClockCommand".
 */
export interface ClockCommand {
  speed?: number;
  paused?: boolean;
  jump_to?: HouseTime;
}
/**
 * Operator request to start a new episode: a fresh world at house time 'start'. Clears live state and starts a new store.
 *
 * This interface was referenced by `Clock`'s JSON-Schema
 * via the `definition` "ResetCommand".
 */
export interface ResetCommand {
  start: HouseTime;
  mode: ClockMode;
  speed: number;
}
