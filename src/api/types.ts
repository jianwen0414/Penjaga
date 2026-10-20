// Shapes the homeowner app consumes. They follow the PRD schemas (§10.4) so the stand-in backend in
// src/standin can be replaced by the real API (Agent + Integration) without touching the screens.

export type Severity = 'info' | 'heads_up' | 'act_soon' | 'urgent';
export type LayerId = 'L1' | 'L2' | 'L3' | 'L4' | 'L5';
export type CaseStatus = 'open' | 'monitoring' | 'action_pending' | 'resolved' | 'dismissed';

export interface FocusCommand {
  layer: 'water' | 'electrical' | 'structure';
  object_ids: string[];
  severity: Severity;
  label?: string;
}

export interface Series {
  unit: string;
  points: { t: number; v: number }[]; // t = epoch ms
  bands?: { from: number; to: number; label: string }[]; // shaded windows (e.g. quiet hours)
  lines?: { v: number; label: string; tone: 'muted' | 'warn' | 'danger' }[]; // thresholds / baselines
  markers?: { t: number; label: string }[]; // e.g. rain
}

export interface TimelineEvent {
  t: number;
  actor: 'penjaga' | 'owner' | 'vendor' | 'management';
  text: string;
}

export interface Action {
  id: string;
  kind: 'guided_check' | 'book_vendor' | 'send_management' | 'dismiss' | 'show_in_3d';
  label: string;
  detail?: string;
  primary?: boolean;
  vendor_category?: VendorCategory;
  focus?: FocusCommand;
}

export interface GuidedStep {
  kind?: 'valve' | 'breaker'; // close a valve (water) or switch off a breaker (earth leakage)
  fixture_id: string; // what is tested: a fixture, or a circuit
  fixture_label: string;
  valve_id?: string; // the valve the owner closes (set by the Core API)
  circuit_id?: string; // the circuit the owner switches off, and its breaker
  breaker_id?: string;
  instruction: string;
  result?: 'stopped' | 'continued'; // stopped: the flow or the leakage went with it (found it)
  flow_before?: number;
  flow_after?: number;
  residual_before?: number;
  residual_after?: number;
}

export interface Case {
  id: string;
  status: CaseStatus;
  layer: LayerId;
  severity: Severity;
  title: string;
  location: string;
  summary: string;
  confidence: number; // 0..1 — simulated until the trained models land (see README)
  confidence_note: string;
  impact: { headline: string; lines: string[]; how?: string };
  safety?: string[];
  actions: Action[];
  evidence: { title: string; series: Series; stats: { label: string; value: string }[]; explanation: string };
  guided?: { intro: string; steps: GuidedStep[]; done: boolean; located?: string };
  booking?: Booking;
  timeline: TimelineEvent[];
  focus: FocusCommand;
  opened_at: number;
  resolved_at?: number;
  outcome?: string;
  /** The owner shut the supply off in an emergency: quiet readings mean "off", not "fixed", until the repair. */
  contained?: { what: 'main_stop_valve' | 'breaker'; at: number };
}

export type VendorCategory = 'plumber' | 'electrician' | 'aircon_service';

export interface Vendor {
  id: string;
  name: string;
  category: VendorCategory;
  rating: number;
  jobs: number;
  price: string;
  licence?: string;
  area: string;
  next_slots: number[];
  illustrative?: boolean; // fictional vendor for this prototype (the Core API always sets it)
}

export interface Booking {
  id?: string; // set by the Core API (the Lab completes jobs by it)
  vendor_id: string;
  vendor_name: string;
  slot: number;
  status: 'requested' | 'acknowledged' | 'completed';
  created_at: number;
}

export interface Notice {
  id: string;
  type: 'water_disruption' | 'planned_power_outage' | 'works' | 'general';
  title: string;
  issuer: string;
  area: string;
  starts_at: number;
  ends_at: number;
  body: string;
  impact?: string; // Penjaga's reading of what it means for this house
}

export interface Wellbeing {
  id: string;
  title: string;
  body: string;
  since: number;
  person: string;
}

export interface MaintenanceItem {
  id: string;
  title: string;
  object_id: string;
  due: number;
  interval: string;
  source: string;
}

export interface PassportEntry {
  t: number;
  title: string;
  detail: string;
  case_id: string;
}

export interface HomeSummary {
  status: 'all_quiet' | 'attention' | 'urgent';
  checked_at: number;
  today: {
    water_l: number; water_typical_l: number; water_series: number[];
    elec_kwh: number; elec_typical_kwh: number; elec_series: number[];
  };
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'penjaga';
  text: string;
  chips?: { label: string; focus?: FocusCommand; to?: string; drill?: boolean }[];
  t: number;
}
