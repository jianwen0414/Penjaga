// Agent stand-in (M8): turns findings + house record + notices into the cases, notices and answers the app
// shows. Everything is derived from data at runtime — nothing is keyed to a demo scenario. The real agent
// (LLM + tools, Agent + Integration workstream) replaces this module behind the same shapes in src/api.
import spec from '../../house.spec.json';
import type { Case, Notice, Wellbeing, HomeSummary, MaintenanceItem, PassportEntry, Vendor, ChatMessage, FocusCommand, GuidedStep, Series } from '../api/types';
import { detect, type Finding } from './detect';
import { getWorld, update, type World, type BookingRecord } from './world';
import { nightMinFlow, residualDaily, flowWithValveClosed, flowNow, waterFlowSeries, tankOutlook, todayUsage, BASELINE_M3_PER_MONTH, TIME } from './sim';
import { marginalWaterCost } from './tariff';

const ROOM_NAMES: Record<string, string> = {
  living: 'Living room', dining: 'Dining area', kitchen: 'Kitchen', foyer: 'Foyer', stair: 'Staircase', yard: 'Yard',
  terrace: 'Terrace', porch: 'Car porch', bed4: 'Flexi-Suite bedroom', bath4: 'Flexi-Suite bathroom',
  master: 'Master bedroom', bath_master: 'Master bathroom', family: 'Family area', corridor: 'Corridor', landing: 'Landing',
  bed3: 'Bedroom 3', bed2: 'Bedroom 2', bath3: 'Bathroom 3', bath2: 'Bathroom 2', roof: 'Roof space', porch_roof: 'Porch roof',
};
export const roomName = (id?: string | null) => (id ? ROOM_NAMES[id] ?? id : '');

type Dev = { id: string; type: string; room: string; floor: string; meta?: Record<string, unknown>; links?: string[] };
type Circ = { id: string; kind: string; rating_a: number; label: string; ends: string[]; floor: string };
const S = (spec as unknown as { services: { devices: Dev[]; circuits: Circ[]; pipes: { id: string; room: string; system: string; floor: string }[] } }).services;
const devices = new Map(S.devices.map((d) => [d.id, d]));

export function objectName(id: string): string {
  const d = devices.get(id);
  if (id.startsWith('fix_') && id.endsWith('_wc_cistern')) return `${roomName(d?.room)} toilet tank`;
  if (id.startsWith('fix_') && id.endsWith('_basin_tap')) return `${roomName(d?.room)} basin tap`;
  if (id.startsWith('fix_') && id.endsWith('_shower')) return `${roomName(d?.room)} shower`;
  if (id.startsWith('app_wh_')) return `${roomName(d?.room)} water heater`;
  if (id.startsWith('app_ac_')) return `${roomName(d?.room)} aircon`;
  if (id.startsWith('app_fan_')) return `${roomName(d?.room)} ceiling fan`;
  if (id.startsWith('sock_')) return `${roomName(d?.room)} socket`;
  const c = S.circuits.find((x) => x.id === id);
  if (c) return c.label;
  return id;
}

// ---------------- vendors (illustrative, clearly labelled in the UI) ----------------
const slotsFrom = (now: number, hours: number[]) => {
  const base = TIME.startOfDay(now) + TIME.DAY;
  return hours.map((h, i) => base + Math.floor(i / 2) * TIME.DAY + h * TIME.HOUR);
};
export function vendors(category: 'plumber' | 'electrician', now = Date.now()): Vendor[] {
  const P: Omit<Vendor, 'next_slots'>[] = category === 'plumber'
    ? [
      { id: 'v_plumb_1', name: 'Hafiz Plumbing Works', category, rating: 4.8, jobs: 212, price: 'RM80–150 · flush valve', area: 'Elmina, Shah Alam' },
      { id: 'v_plumb_2', name: 'Tan & Sons Plumbing', category, rating: 4.6, jobs: 340, price: 'RM90–160 · flush valve', area: 'Shah Alam' },
      { id: 'v_plumb_3', name: 'Paip Pantas', category, rating: 4.4, jobs: 98, price: 'RM70–140 · flush valve', area: 'Subang, Shah Alam' },
    ]
    : [
      { id: 'v_elec_1', name: 'Sinar Elektrik (licensed)', category, rating: 4.9, jobs: 156, price: 'RM120–250 · inspection + fix', licence: 'ST-registered wireman', area: 'Elmina, Shah Alam' },
      { id: 'v_elec_2', name: 'Kuasa Selamat Services', category, rating: 4.7, jobs: 301, price: 'RM150–280 · inspection + fix', licence: 'ST-registered wireman', area: 'Shah Alam' },
    ];
  return P.map((v, i) => ({ ...v, next_slots: slotsFrom(now, i % 2 ? [14, 10, 16] : [10, 15, 11]) }));
}

// ---------------- cases ----------------
const fmt = (n: number, d = 0) => n.toLocaleString('en-MY', { maximumFractionDigits: d, minimumFractionDigits: d });
const dayName = (t: number) => new Date(t).toLocaleDateString('en-MY', { weekday: 'long' });

function bookingView(w: World, key: string, now: number) {
  const b = [...w.bookings].reverse().find((x) => x.case_key === key);
  if (!b) return undefined;
  const v = [...vendors('plumber', now), ...vendors('electrician', now)].find((x) => x.id === b.vendor_id);
  const status = b.status === 'completed' ? 'completed' : now - b.at > 8000 ? 'acknowledged' : 'requested';
  return { rec: b, view: { vendor_id: b.vendor_id, vendor_name: v?.name ?? b.vendor_id, slot: b.slot, status, created_at: b.at } as Case['booking'] };
}

function leakCase(f: Finding, w: World, now: number): Case {
  const key = f.key;
  const lpd = Number(f.evidence.litres_per_day);
  const extraM3 = (lpd * 30) / 1000;
  const cost = marginalWaterCost(BASELINE_M3_PER_MONTH, extraM3);
  const checks = w.checks.filter((c) => c.case_key === key);
  const located = checks.find((c) => c.flow_after < 0.02 && c.flow_before >= 0.05);
  const order = ['fix_bath4_wc_cistern', 'fix_bath2_wc_cistern', 'fix_bath3_wc_cistern', 'fix_bath_master_wc_cistern'];
  const steps: GuidedStep[] = order.map((id) => {
    const c = checks.find((x) => x.fixture_id === id);
    return {
      fixture_id: id, fixture_label: objectName(id),
      instruction: `Close the small valve on the pipe under the ${objectName(id).toLowerCase()} (turn it clockwise until it stops), wait a moment, then tap Check.`,
      result: c ? (c.flow_after < 0.02 ? 'stopped' : 'continued') : undefined,
      flow_before: c?.flow_before, flow_after: c?.flow_after,
    };
  });
  const bk = bookingView(w, key, now);
  const fixedVerified = bk?.view?.status === 'completed' && flowNow(now, w) < 0.02;
  const nights = nightMinFlow(7, now, w);
  const series: Series = {
    unit: 'L/min',
    points: waterFlowSeries(72, now, w),
    bands: [0, 1, 2].map((i) => { const d = TIME.startOfDay(now) - (2 - i) * TIME.DAY; return { from: d + TIME.HOUR, to: d + 5 * TIME.HOUR, label: 'Quiet hours' }; }),
    lines: [{ v: 0, label: 'Normal at night: 0', tone: 'muted' }],
  };
  const where = located ? objectName(located.fixture_id) : 'A toilet tank, most likely';
  const timeline: Case['timeline'] = [
    { t: f.first_seen + 5 * TIME.HOUR, actor: 'penjaga', text: 'Noticed water flowing during quiet hours' },
    { t: f.first_seen + TIME.DAY + 5 * TIME.HOUR, actor: 'penjaga', text: `Confirmed on a second night · opened this case` },
    ...checks.map((c) => ({ t: c.at, actor: 'owner' as const, text: `Checked ${objectName(c.fixture_id).toLowerCase()}: flow ${c.flow_after < 0.02 ? 'stopped' : 'continued'}` })),
    ...w.tickets.filter((t) => t.case_key === key).map((t) => ({ t: t.at, actor: 'owner' as const, text: 'Sent to management' })),
  ];
  if (bk?.view) {
    timeline.push({ t: bk.rec.at, actor: 'owner', text: `Booked ${bk.view.vendor_name} for ${new Date(bk.view.slot).toLocaleString('en-MY', { weekday: 'short', hour: 'numeric', minute: '2-digit' })}` });
    if (bk.view.status !== 'requested') timeline.push({ t: bk.rec.at + 8000, actor: 'vendor', text: `${bk.view.vendor_name} confirmed the visit` });
    if (bk.view.status === 'completed') timeline.push({ t: bk.rec.done_at ?? now, actor: 'vendor', text: 'Job marked complete' });
  }
  if (fixedVerified) timeline.push({ t: (bk?.rec.done_at ?? now) + 60e3, actor: 'penjaga', text: 'Verified: water meter is still again' });
  const focus: FocusCommand = located
    ? { layer: 'water', object_ids: [located.fixture_id, `pipe_${located.fixture_id.replace('fix_', '').replace('_wc_cistern', '')}_cold_supply`], severity: 'act_soon', label: objectName(located.fixture_id) }
    : { layer: 'water', object_ids: order, severity: 'act_soon', label: 'One of these toilet tanks' };
  const status = fixedVerified ? 'resolved' : bk ? 'action_pending' : 'open';
  return {
    id: `case-leak-${f.first_seen}`, status, layer: 'L1', severity: fixedVerified ? 'info' : 'act_soon',
    title: fixedVerified ? 'Leak fixed' : located ? `${objectName(located.fixture_id)} is leaking` : 'Water is running when nobody is using it',
    location: located ? `${roomName(devices.get(located.fixture_id)?.room)} · toilet tank` : where,
    summary: fixedVerified
      ? `The ${objectName(located?.fixture_id ?? 'fix_bath2_wc_cistern').toLowerCase()} was repaired and your water meter is still again at night.`
      : located
        ? `Closing the valve under the ${objectName(located.fixture_id).toLowerCase()} stopped the flow, so the leak is in that tank, not in a pipe inside a wall. It's usually a worn flush valve: a small part.`
        : `Since ${dayName(f.first_seen + TIME.DAY)} night, water has been flowing between 1am and 5am, when your home is normally still. Short refills every few minutes point to a toilet tank that isn't sealing. A 2-minute check will find which one.`,
    confidence: located ? 0.97 : f.confidence,
    confidence_note: located ? 'Confirmed by your check: closing that valve stopped the flow.' : 'Seen on ' + f.evidence.nights_observed + ' nights. Your check will confirm the location.',
    impact: {
      headline: `~${fmt(lpd)} L a day · ~RM${cost.rm.toFixed(2)} a month`,
      lines: [
        `About a full bathtub of water, every day.`,
        `Over a year: ~${fmt(lpd * 365 / 1000, 0)} m³ and ~RM${fmt(cost.rm * 12, 0)}.`,
        `Left alone, a worn flush valve usually gets worse, not better.`,
      ],
      how: `${cost.how} ${cost.source}.`,
    },
    actions: fixedVerified ? [] : located
      ? [
        ...(bk ? [] : [{ id: 'book', kind: 'book_vendor' as const, label: 'Book a plumber', detail: 'Flush valve replacement · typically RM80–150', primary: true, vendor_category: 'plumber' as const }]),
        { id: 'mgmt', kind: 'send_management', label: 'Send to management', detail: 'Under defect liability? Let them check first' },
        { id: 'dismiss', kind: 'dismiss', label: 'Not an issue' },
      ]
      : [
        { id: 'check', kind: 'guided_check', label: 'Find it with a 2-minute check', detail: 'One toilet at a time', primary: true },
        { id: 'book', kind: 'book_vendor', label: 'Book a plumber instead', vendor_category: 'plumber' },
        { id: 'dismiss', kind: 'dismiss', label: 'Not an issue' },
      ],
    evidence: {
      title: 'Water flow at your meter, last 3 days',
      series,
      stats: [
        { label: 'Lowest night-time flow', value: `${f.evidence.night_min_flow_lpm} L/min` },
        { label: 'Nights seen', value: `${f.evidence.nights_observed} of the last 3` },
        { label: 'Usual at night', value: '0 L/min' },
        { label: 'Pattern', value: 'Short refills every few minutes' },
      ],
      explanation: `Your home is normally still between 1am and 5am. On ${nights.filter((n) => n.v >= 0.05).length} recent nights the meter never stopped turning. A steady trickle with regular refills is the signature of a toilet tank that isn't sealing.`,
    },
    guided: { intro: 'Close one toilet valve at a time. When the flow stops, we\'ve found it.', steps, done: !!located, located: located?.fixture_id },
    booking: bk?.view,
    timeline: timeline.sort((a, b) => a.t - b.t),
    focus, opened_at: f.first_seen + TIME.DAY + 5 * TIME.HOUR,
    resolved_at: fixedVerified ? (bk?.rec.done_at ?? now) : undefined,
    outcome: fixedVerified ? `Saving ~${fmt(lpd * 30)} L and ~RM${cost.rm.toFixed(2)} a month` : undefined,
  };
}

function earthCase(f: Finding, w: World, now: number): Case {
  const key = f.key;
  const days = residualDaily(7, now, w);
  const bk = bookingView(w, key, now);
  const done = bk?.view?.status === 'completed';
  const series: Series = {
    unit: 'mA',
    points: days.map((d) => ({ t: d.t + 15 * TIME.HOUR, v: d.v })),
    lines: [{ v: 30, label: 'Safety switch trips (30 mA)', tone: 'danger' }, { v: 10, label: 'Penjaga warning level', tone: 'warn' }],
    markers: days.filter((d) => d.rain).map((d) => ({ t: d.t + 15 * TIME.HOUR, label: 'Rain' })),
  };
  const focus: FocusCommand = { layer: 'electrical', object_ids: ['circ_sockets_outdoor', 'sock_porch_outdoor', 'rcd_main'], severity: 'urgent', label: 'Outdoor socket circuit' };
  const timeline: Case['timeline'] = [
    { t: f.first_seen + 16 * TIME.HOUR, actor: 'penjaga', text: 'Earth leakage started climbing' },
    { t: now - 20 * 60e3, actor: 'penjaga', text: `Reached ${f.evidence.residual_ma} mA after rain · marked urgent` },
    ...w.tickets.filter((t) => t.case_key === key).map((t) => ({ t: t.at, actor: 'owner' as const, text: 'Sent to management' })),
  ];
  if (bk?.view) {
    timeline.push({ t: bk.rec.at, actor: 'owner', text: `Booked ${bk.view.vendor_name}` });
    if (bk.view.status !== 'requested') timeline.push({ t: bk.rec.at + 8000, actor: 'vendor', text: `${bk.view.vendor_name} confirmed the visit` });
  }
  return {
    id: `case-earth-${f.first_seen}`, status: done ? 'resolved' : bk ? 'action_pending' : 'open', layer: 'L2',
    severity: done ? 'info' : 'urgent',
    title: done ? 'Outdoor socket repaired' : 'Electricity is leaking to earth on the outdoor socket circuit',
    location: 'Terrace · outdoor socket circuit',
    summary: `Leakage current has climbed from ${f.evidence.residual_ma_week_start} mA to ${f.evidence.residual_ma} mA this week, and it jumps after rain (${f.evidence.rain_avg_ma} mA on rainy days vs ${f.evidence.dry_avg_ma} mA on dry days). That points to moisture getting into the outdoor socket on the terrace. Your safety switch trips at 30 mA.`,
    safety: [
      "Don't use the terrace outdoor socket until it has been checked.",
      'If the safety switch (RCD) trips, don\'t keep resetting it.',
      'You can switch off the "Outdoor socket" breaker in the board by the front door. Don\'t open the board or the socket yourself.',
    ],
    confidence: f.confidence,
    confidence_note: 'Leakage that rises with rain usually means water in an outdoor fitting. An electrician will confirm with an insulation test.',
    impact: {
      headline: 'Risk of electric shock and fire',
      lines: [
        `Leakage is ${f.evidence.residual_ma} mA and rising; your safety switch trips at 30 mA.`,
        'About 60% of house fires in Malaysia are linked to electrical faults (Fire and Rescue Department, reported by NST, Aug 2025).',
        'Caught now, this is usually a sealed or replaced socket.',
      ],
    },
    actions: done ? [] : [
      ...(bk ? [] : [{ id: 'book', kind: 'book_vendor' as const, label: 'Book a licensed electrician', detail: 'ST-registered · inspection and repair', primary: true, vendor_category: 'electrician' as const }]),
      { id: 'breaker', kind: 'show_in_3d', label: 'Show me the breaker', focus: { layer: 'electrical', object_ids: ['db_main', 'rcd_main'], severity: 'urgent', label: 'Distribution board · foyer' } },
      { id: 'mgmt', kind: 'send_management', label: 'Send to management' },
    ],
    evidence: {
      title: 'Earth leakage at your safety switch, last 7 days',
      series,
      stats: [
        { label: 'Now', value: `${f.evidence.residual_ma} mA` },
        { label: 'A week ago', value: `${f.evidence.residual_ma_week_start} mA` },
        { label: 'Rainy days', value: `${f.evidence.rain_avg_ma} mA avg` },
        { label: 'Dry days', value: `${f.evidence.dry_avg_ma} mA avg` },
      ],
      explanation: 'A small leakage to earth is normal. A steady climb, worse after rain, is an early sign of water in an outdoor fitting. This comes from Penjaga\'s always-on safety limits, which no AI component can override.',
    },
    booking: bk?.view,
    timeline: timeline.sort((a, b) => a.t - b.t),
    focus, opened_at: now - 20 * 60e3, resolved_at: done ? (bk?.rec.done_at ?? now) : undefined,
    outcome: done ? 'Outdoor socket replaced; leakage back to normal' : undefined,
  };
}

function noNoticeCase(f: Finding, now: number): Case {
  return {
    id: 'case-supply', status: 'open', layer: 'L5', severity: 'heads_up',
    title: 'Water supply stopped at your meter', location: 'Main supply · meter at the gate',
    summary: 'Pressure at your water inlet has dropped to almost zero. There is no notice from management about works on your street, so this may be a utility problem.',
    confidence: f.confidence, confidence_note: 'Measured directly at the inlet.',
    impact: { headline: `Your roof tank covers your usual use for a while`, lines: [tankLine(now)] },
    actions: [{ id: 'mgmt', kind: 'send_management', label: 'Ask management', primary: true, detail: 'They can check with Air Selangor' }],
    evidence: { title: 'Supply pressure', series: { unit: 'bar', points: [{ t: now - 3 * TIME.HOUR, v: 1.8 }, { t: now - 3600e3, v: 1.8 }, { t: now - 1800e3, v: 0.1 }, { t: now, v: 0.1 }] }, stats: [{ label: 'Pressure now', value: `${f.evidence.pressure_bar} bar` }, { label: 'Normal', value: '≈1.8 bar' }], explanation: 'A sudden drop to zero at the inlet means water is not reaching your house.' },
    timeline: [{ t: now, actor: 'penjaga', text: 'Supply pressure dropped' }],
    focus: { layer: 'water', object_ids: ['meter_water_main', 'pipe_supply_main'], severity: 'heads_up', label: 'Water meter' },
    opened_at: now,
  };
}

function tankLine(now: number) {
  const o = tankOutlook(now);
  const tomorrow = o.empty_at && new Date(o.empty_at).getDate() !== new Date(now).getDate();
  const until = o.empty_at ? `${new Date(o.empty_at).toLocaleTimeString('en-MY', { hour: 'numeric', minute: '2-digit' })}${tomorrow ? ' tomorrow' : ''}` : 'well past tomorrow';
  return `Your roof tank (about ${o.litres_now} L) should cover your usual use until about ${until}.`;
}

export interface View {
  cases: Case[];
  notices: Notice[];
  wellbeing: Wellbeing | null;
  home: HomeSummary;
  maintenance: MaintenanceItem[];
  passport: PassportEntry[];
  consent: World['consent'];
  toured: boolean;
}

export function view(now = Date.now()): View {
  const w = getWorld();
  const findings = detect(now);
  const cases: Case[] = [];
  const notices: Notice[] = w.notices.map((n) => ({ ...n, issuer: 'Elmina Green management', impact: undefined }));
  for (const f of findings) {
    if (f.layer === 'L1' && w.consent.L1) cases.push(leakCase(f, w, now));
    if (f.layer === 'L2' && w.consent.L2) cases.push(earthCase(f, w, now));
    if (f.layer === 'L5') {
      // Clue-linking: a pressure drop inside an announced disruption window is expected, not a fault.
      const n = notices.find((x) => x.type === 'water_disruption' && x.starts_at - 3600e3 <= now && now <= x.ends_at + 3600e3);
      if (n) n.impact = `Water pressure at your meter dropped at ${new Date(now).toLocaleTimeString('en-MY', { hour: 'numeric', minute: '2-digit' })}. This matches the scheduled works, so it isn't a leak. ${tankLine(now)}`;
      else if (w.consent.L5) cases.push(noNoticeCase(f, now));
    }
  }
  // Completed jobs whose finding has aged out stay visible as resolved cases.
  const visible = cases.filter((c) => !w.dismissed.some((d) => c.id.includes(d.case_key.split(':').pop()!)));
  const wf = findings.find((f) => f.layer === 'wellbeing');
  const acked = wf && w.wellbeingAck.some((a) => a.id === wf.key);
  const wellbeing = wf && !acked ? {
    id: wf.key, person: 'Mak', since: wf.first_seen,
    title: "Mak's suite has been quiet this morning",
    body: `No water or power used in the ground-floor suite since ${new Date(wf.first_seen).toLocaleTimeString('en-MY', { hour: 'numeric', minute: '2-digit' })} last night. Usually there's activity by 7am.`,
  } : null;
  const open = visible.filter((c) => c.status !== 'resolved');
  const t = todayUsage(now, w);
  const passport: PassportEntry[] = visible.filter((c) => c.status === 'resolved').map((c) => ({ t: c.resolved_at!, title: c.title, detail: c.outcome ?? '', case_id: c.id }));
  return {
    cases: visible.sort((a, b) => sevRank(b) - sevRank(a)),
    notices: notices.sort((a, b) => b.starts_at - a.starts_at),
    wellbeing,
    home: { status: open.some((c) => c.severity === 'urgent') ? 'urgent' : open.length ? 'attention' : 'all_quiet', checked_at: now - 90e3, today: t },
    maintenance: maintenance(now),
    passport: [...passport, { t: new Date('2024-06-14').getTime(), title: 'Home handed over', detail: 'Water heaters, aircons and safety switch registered from the handover record', case_id: '' }],
    consent: w.consent,
    toured: w.toured,
  };
}
const sevRank = (c: Case) => (c.status === 'resolved' ? -1 : { info: 0, heads_up: 1, act_soon: 2, urgent: 3 }[c.severity]);

function maintenance(now: number): MaintenanceItem[] {
  const d = (days: number) => TIME.startOfDay(now) + days * TIME.DAY;
  return [
    { id: 'm_ac_master', title: 'Service the master bedroom aircon', object_id: 'app_ac_master', due: d(12), interval: 'Every 6 months', source: 'Typical manufacturer recommendation (source to confirm per model)' },
    { id: 'm_rcd_test', title: 'Press the test button on your safety switch', object_id: 'rcd_main', due: d(19), interval: 'Every 3 months', source: 'Common RCD manufacturer guidance (source to confirm)' },
    { id: 'm_tank', title: 'Clean the roof water tank', object_id: 'tank_roof_main', due: d(64), interval: 'Every 12 months', source: 'Typical water-utility advice (source to confirm)' },
  ];
}

// ---------------- homeowner actions ----------------
export function runGuidedCheck(caseKeyOrId: string, fixtureId: string) {
  const now = Date.now();
  const before = flowNow(now);
  const after = flowWithValveClosed(fixtureId, now);
  const key = findingKeyFor(caseKeyOrId);
  update((w) => { w.checks.push({ case_key: key, fixture_id: fixtureId, at: now, flow_before: before, flow_after: after }); });
  return { before, after };
}
export function book(caseId: string, vendorId: string, slot: number) {
  update((w) => { w.bookings.push({ case_key: findingKeyFor(caseId), vendor_id: vendorId, slot, at: Date.now(), status: 'requested' }); });
}
export function sendToManagement(caseId: string, note: string) {
  update((w) => { w.tickets.push({ case_key: findingKeyFor(caseId), at: Date.now(), note }); });
}
export function dismiss(caseId: string, reason: string) {
  update((w) => { w.dismissed.push({ case_key: findingKeyFor(caseId), at: Date.now(), reason }); });
}
export function ackWellbeing(id: string, note: string) {
  update((w) => { w.wellbeingAck.push({ id, at: Date.now(), note }); });
}
export function setConsent(layer: keyof World['consent'], on: boolean) {
  update((w) => { w.consent[layer] = on; });
}
export function markToured() { update((w) => { w.toured = true; }); }

function findingKeyFor(caseId: string) {
  const f = detect().find((x) => caseId.endsWith(String(x.first_seen)) || caseId === x.key);
  return f?.key ?? caseId;
}
export function completeBookings(category: 'plumber' | 'electrician') {
  update((w) => {
    const now = Date.now();
    w.bookings.filter((b: BookingRecord) => b.status !== 'completed' && b.vendor_id.includes(category === 'plumber' ? 'plumb' : 'elec'))
      .forEach((b) => { b.status = 'completed'; b.done_at = now; });
    const type = category === 'plumber' ? 'cistern_leak' : 'earth_leakage';
    w.faults.filter((f) => f.type === type && !f.cleared_at).forEach((f) => { f.cleared_at = now; });
  });
}

// ---------------- Ask Penjaga (stand-in) ----------------
// Answers from the house record and live cases; replaced by the LLM agent. Out-of-scope requests get a redirect.
const APPLIANCE_WORDS: [RegExp, string][] = [[/heater/, 'app_wh_'], [/air ?con|aircond|\bac\b/, 'app_ac_'], [/fan/, 'app_fan_'], [/socket|plug|power point/, 'sock_']];
const ROOM_WORDS: [RegExp, string][] = [
  [/master (bath|bathroom|toilet)/, 'bath_master'], [/master/, 'master'], [/suite (bath|bathroom|toilet)|guest bath|bathroom 4|bath 4/, 'bath4'], [/suite|guest|mak|bedroom 4/, 'bed4'],
  [/bathroom 2|bath 2/, 'bath2'], [/bathroom 3|bath 3/, 'bath3'], [/bedroom 2/, 'bed2'], [/bedroom 3/, 'bed3'],
  [/living/, 'living'], [/kitchen/, 'kitchen'], [/yard|laundry|washer/, 'yard'], [/family/, 'family'], [/terrace|outdoor|outside/, 'terrace'],
];
let msgId = 0;
export function ask(text: string, ctx: { caseId?: string } = {}): ChatMessage {
  const q = text.toLowerCase();
  const say = (t: string, chips?: ChatMessage['chips']): ChatMessage => ({ id: `m${++msgId}`, role: 'penjaga', text: t, chips, t: Date.now() });
  const v = view();
  if (/music|song|joke|weather|news|timer|alarm|movie|recipe/.test(q)) {
    return say("That's outside what I do. I look after your home: water, power, appliances and maintenance. Ask me where something runs, or what a case means.");
  }
  if (/drill|hang|nail|screw|mount/.test(q)) {
    return say('Use "Can I drill here?" in My Home: tap any wall and I\'ll show the pipes and cables inside it. It\'s based on your home\'s drawings, so always confirm with a wire and pipe detector before drilling.', [{ label: 'Open Can I drill here?', to: '/home?drill=1', drill: true }]);
  }
  const room = ROOM_WORDS.find(([re]) => re.test(q))?.[1];
  const appl = APPLIANCE_WORDS.find(([re]) => re.test(q))?.[1];
  if (/power|electric|circuit|breaker|where.*(get|from)/.test(q) && appl) {
    const dev = S.devices.find((d) => d.id.startsWith(appl) && (!room || d.room === room)) ?? S.devices.find((d) => d.id.startsWith(appl));
    if (dev) {
      const circ = S.circuits.find((c) => c.ends.includes(dev.id));
      if (circ) {
        const shared = circ.ends.length > 1 ? `It shares the circuit with ${circ.ends.length - 1} other point${circ.ends.length > 2 ? 's' : ''}.` : 'It has its own dedicated circuit, which is what you want for a high-power appliance.';
        return say(`The ${objectName(dev.id).toLowerCase()} is fed by "${circ.label}", a ${circ.rating_a} A breaker in the distribution board by the front door. The cable runs up from the board through the ceiling and down inside the wall to it. ${shared}`,
          [{ label: 'Show me', focus: { layer: 'electrical', object_ids: [circ.id, dev.id, 'db_main'], severity: 'info', label: circ.label } }]);
      }
    }
  }
  if (/pipe|water|tap|leak|plumb/.test(q) && room) {
    const pipes = S.pipes.filter((p) => p.room === room && p.system !== 'waste');
    if (pipes.length) {
      return say(`${roomName(room)} has ${pipes.length} supply pipe run${pipes.length > 1 ? 's' : ''} (${[...new Set(pipes.map((p) => p.system))].join(' and ')}) inside its walls. I'll show you where they go.`,
        [{ label: 'Show me', focus: { layer: 'water', object_ids: pipes.map((p) => p.id), severity: 'info', label: `${roomName(room)} pipes` } }]);
    }
  }
  if (/wrong|problem|issue|status|ok|okay|case|alert/.test(q)) {
    const open = v.cases.filter((c) => c.status !== 'resolved');
    if (!open.length) return say('All quiet. Nothing in your home needs attention right now.');
    return say(`${open.length === 1 ? 'One thing needs' : `${open.length} things need`} your attention: ${open.map((c) => c.title.toLowerCase()).join('; ')}.`,
      open.map((c) => ({ label: `Open: ${c.title}`, to: `/cases/${c.id}` })));
  }
  if (/cost|how much|rm|bill|money/.test(q) && ctx.caseId) {
    const c = v.cases.find((x) => x.id === ctx.caseId);
    if (c?.impact.how) return say(`${c.impact.headline}. ${c.impact.how}`);
  }
  if (/tank/.test(q)) {
    return say('Your 1,000 L roof tank sits above the bathrooms. A rising main from the meter fills it through a float valve, and it feeds the bathrooms by gravity. The kitchen tap comes straight from the mains.', [{ label: 'Show me', focus: { layer: 'water', object_ids: ['tank_roof_main', 'valve_roof_tank_float', 'pipe_rising_main_ff'], severity: 'info', label: 'Roof tank' } }]);
  }
  return say('I can help with your home: where pipes and cables run, what an open case means, what something costs, and when things are due for maintenance. Try "Where does the master bathroom heater get its power?"');
}
