/* GENERATED from packages/contract/src/penjaga_contract/schemas by packages/contract/tools/generate.py. Do not edit by hand. */

/**
 * Runs from the Lab use validation or the sealed test split (tuning uses train and validation from the CLI).
 *
 * This interface was referenced by `EvalApi`'s JSON-Schema
 * via the `definition` "EvalSplit".
 */
export type EvalSplit = "validation" | "test";
/**
 * Preventive layer: L1 leaks, L2 electrical safety, L3 appliance health, L4 maintenance, L5 management integration.
 */
export type Layer = "L1" | "L2" | "L3" | "L4" | "L5";
export type DetectorName = string;
export type DetectorName1 = string;
/**
 * This interface was referenced by `EvalApi`'s JSON-Schema
 * via the `definition` "EvalRunStatus".
 */
export type EvalRunStatus = "queued" | "running" | "complete" | "failed";
export type DetectorName2 = string;

/**
 * LAB NAMESPACE: the M11 evaluation service /eval/v1 (operator role only), used by the Lab's evaluation view. A run replays a suite's episodes through detectors and scores them against ground truth; test-split runs use the sealed manifests and are logged with the git commit.
 */
export interface EvalApi {
  [k: string]: unknown;
}
/**
 * GET /eval/v1/suites.
 *
 * This interface was referenced by `EvalApi`'s JSON-Schema
 * via the `definition` "EvalSuite".
 */
export interface EvalSuite {
  name: string;
  /**
   * @minItems 1
   */
  layers: [Layer, ...Layer[]];
  days: number;
  episodes: SplitCounts;
  /**
   * The test split's manifests are sealed for the current generator.
   */
  sealed: boolean;
  generator: string;
  /**
   * Detectors that can be evaluated on this suite (its layers). The deployed ones are the default.
   */
  detectors: DetectorName[];
}
/**
 * Episodes per split.
 */
export interface SplitCounts {
  train?: number;
  validation?: number;
  test?: number;
}
/**
 * POST /eval/v1/runs.
 *
 * This interface was referenced by `EvalApi`'s JSON-Schema
 * via the `definition` "EvalRunRequest".
 */
export interface EvalRunRequest {
  suite: string;
  split: EvalSplit;
  /**
   * Null: the deployed detectors for the suite's layers.
   *
   * @minItems 1
   */
  detectors?: [DetectorName1, ...DetectorName1[]] | null;
  /**
   * Only the first N episodes (validation only; a test run always uses the whole sealed split).
   */
  limit?: number | null;
}
/**
 * This interface was referenced by `EvalApi`'s JSON-Schema
 * via the `definition` "LayerHeadline".
 */
export interface LayerHeadline {
  recall: number | null;
  recall_located: number | null;
  precision: number | null;
  false_alarms_per_house_month: number | null;
  faults: number;
  detected: number;
  by_fault_type?: {
    [k: string]: FaultTypeHeadline;
  };
}
export interface FaultTypeHeadline {
  n: number;
  recall: number | null;
  ttd_hours_median?: number | null;
}
/**
 * This interface was referenced by `EvalApi`'s JSON-Schema
 * via the `definition` "DetectorResult".
 */
export interface DetectorResult {
  version: string;
  layers: {
    [k: string]: LayerHeadline;
  };
}
/**
 * GET /eval/v1/runs/{run_id}. The full report is at /eval/v1/runs/{run_id}/report (JSON) and /report.md (Markdown) once complete.
 *
 * This interface was referenced by `EvalApi`'s JSON-Schema
 * via the `definition` "EvalRun".
 */
export interface EvalRun {
  run_id: string;
  suite: string;
  split: EvalSplit;
  /**
   * @minItems 1
   */
  detectors: [DetectorName2, ...DetectorName2[]];
  limit?: number | null;
  status: EvalRunStatus;
  /**
   * Wall-clock time (UTC), not house time.
   */
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
  generator?: string | null;
  episodes?: number | null;
  git_commit?: string | null;
  /**
   * Headline metrics per detector and layer, once complete.
   */
  results?: {
    [k: string]: DetectorResult;
  } | null;
}
