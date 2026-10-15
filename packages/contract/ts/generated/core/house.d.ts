/* GENERATED from packages/contract/src/penjaga_contract/schemas by packages/contract/tools/generate.py. Do not edit by hand. */

/**
 * ID of one house instance.
 */
export type HouseId = string;
/**
 * Reference to an entry in docs/DATA_SOURCES.md (e.g. DS-007).
 */
export type SourceRef = string;
/**
 * Calendar date (RFC 3339 full-date, YYYY-MM-DD) in the house's local time zone.
 */
export type CalendarDate = string;
/**
 * Floor level index: 0 ground floor, increasing upwards.
 */
export type LevelIndex = number;
/**
 * Stable, unique, human-readable ID of a physical element: <kind-prefix>_<descriptor>, lowercase snake_case. The prefix must match the element kind (enforced by the semantic validator).
 */
export type ObjectId = string;
export type RoomKind =
  "bedroom" | "bathroom" | "kitchen" | "living" | "dining" | "utility" | "circulation" | "outdoor" | "roof_space";
/**
 * ID prefix: wall_, ceil_ or duct_ respectively.
 */
export type WallKind = "wall" | "ceiling" | "duct";
export type PipeKind = "service" | "rising_main" | "downfeed" | "branch" | "overflow";
/**
 * Dominant routing of the segment. 'concealed' requires concealed_in to be non-empty.
 */
export type PipeRouting = "exposed" | "concealed" | "underground" | "roof_space";
export type ValveKind = "stop" | "gate" | "angle" | "float";
/**
 * Stable, unique, human-readable ID of a physical element: <kind-prefix>_<descriptor>, lowercase snake_case. The prefix must match the element kind (enforced by the semantic validator).
 */
export type ObjectId1 = string;
export type FixtureKind =
  | "wc_cistern"
  | "basin_tap"
  | "shower"
  | "bidet_spray"
  | "sink_tap"
  | "washing_machine_tap"
  | "laundry_tap"
  | "garden_tap";
export type SupplyPhases = 1 | 3;
/**
 * RCCB (prefix rcd_) or MCB (prefix mcb_). upstream_device_id forms the protection chain from the main RCCB down to each circuit's MCB.
 *
 * This interface was referenced by `House`'s JSON-Schema
 * via the `definition` "ProtectiveDevice".
 */
export type ProtectiveDevice = ProtectiveDevice1;
export type ProtectiveDeviceKind = "rccb" | "mcb";
/**
 * An ObjectId or null.
 */
export type NullableObjectId = string | null;
/**
 * Tripping characteristic (MCB only).
 */
export type TripCurve = "B" | "C" | "D";
export type Poles = 1 | 2 | 3 | 4;
export type CircuitKind = "lighting" | "socket" | "dedicated";
export type ApplianceKind =
  | "air_conditioner"
  | "water_heater"
  | "refrigerator"
  | "washing_machine"
  | "rice_cooker"
  | "kettle"
  | "microwave"
  | "air_fryer"
  | "television"
  | "router"
  | "gas_hob"
  | "lpg_cylinder"
  | "lpg_hose";
/**
 * Stable, unique, human-readable ID of a physical element: <kind-prefix>_<descriptor>, lowercase snake_case. The prefix must match the element kind (enforced by the semantic validator).
 */
export type ObjectId2 = string;
/**
 * Calendar date (RFC 3339 full-date, YYYY-MM-DD) in the house's local time zone.
 */
export type CalendarDate1 = string;
/**
 * Item of the Penjaga sensor kit. The stream catalogue (streams.json) maps every stream to exactly one sensor; detection may consume catalogue streams only.
 *
 * This interface was referenced by `House`'s JSON-Schema
 * via the `definition` "Sensor".
 */
export type Sensor = Sensor1;
export type SensorKind =
  "water_meter" | "pressure_sensor" | "tank_level_sensor" | "db_monitor" | "smart_plug" | "weather_feed";
/**
 * An ObjectId or null.
 */
export type NullableObjectId1 = string | null;
export type MeterTechnology = "ultrasonic" | "mechanical";
export type MeterAccuracyClass = 1 | 2;
/**
 * design_target: a specification we require of the sensor we would buy. datasheet: taken from a specific product's datasheet.
 */
export type SpecBasis = "design_target" | "datasheet";
export type LevelTechnology = "ultrasonic" | "pressure";

/**
 * Canonical house record (M0): structure, water system, electrical system, appliances and the sensor kit. Every referenceable element has a stable ObjectId whose prefix matches its kind. Cross-references, topology and regulatory rules are checked by the semantic validator (penjaga_contract.validate).
 */
export interface House {
  schema_version: "1.0";
  house_id: HouseId;
  name: string;
  description: string;
  /**
   * True while the layout is representative rather than taken from the official floor plan (PRD §5.1).
   */
  representative: boolean;
  location: Location;
  handover_date: CalendarDate;
  /**
   * @minItems 1
   */
  levels: [Level, ...Level[]];
  /**
   * @minItems 1
   */
  rooms: [Room, ...Room[]];
  walls: Wall[];
  water: WaterSystem;
  electrical: ElectricalSystem;
  appliances: Appliance[];
  /**
   * @minItems 1
   */
  sensors: [Sensor, ...Sensor[]];
}
/**
 * This interface was referenced by `House`'s JSON-Schema
 * via the `definition` "Location".
 */
export interface Location {
  locality: string;
  latitude: number;
  longitude: number;
  /**
   * What the coordinates point at (a public street, never a specific home).
   */
  reference: string;
  /**
   * IANA time zone name.
   */
  timezone: string;
  source_ref?: SourceRef;
}
/**
 * This interface was referenced by `House`'s JSON-Schema
 * via the `definition` "Level".
 */
export interface Level {
  index: LevelIndex;
  name: string;
}
/**
 * This interface was referenced by `House`'s JSON-Schema
 * via the `definition` "Room".
 */
export interface Room {
  id: ObjectId;
  name: string;
  level: LevelIndex;
  kind: RoomKind;
  /**
   * A place where the floor is likely to be wet, in the sense of Electricity Regulations 1994 reg. 36(2). Final circuits supplying equipment here need RCD protection of at most 10 mA.
   */
  wet_location: boolean;
  /**
   * Optional plan rectangle in metres (origin: front-left corner of the plot at ground level; x across the frontage, y towards the rear). To be filled by the M1 owner from the floor plan.
   */
  footprint?: {
    x_m: number;
    y_m: number;
    width_m: number;
    depth_m: number;
  };
}
/**
 * Structural element that can conceal services and can be faded in the 3D focus sequence (PRD §11.2). Only elements that carry or hide referenced services need an ID here; the 3D model may contain more.
 *
 * This interface was referenced by `House`'s JSON-Schema
 * via the `definition` "Wall".
 */
export interface Wall {
  id: ObjectId;
  name: string;
  kind: WallKind;
  /**
   * @minItems 1
   */
  room_ids: [ObjectId, ...ObjectId[]];
  /**
   * @minItems 1
   */
  levels: [LevelIndex, ...LevelIndex[]];
}
/**
 * Water supply as a tree rooted at the measuring meter. Pipes name their upstream element; fixtures and valves name the pipe they sit on. Whether a fixture is fed from the main or the roof tank is derived from the topology, never stored.
 *
 * This interface was referenced by `House`'s JSON-Schema
 * via the `definition` "WaterSystem".
 */
export interface WaterSystem {
  /**
   * @minItems 1
   */
  meters: [WaterMeter, ...WaterMeter[]];
  tanks: Tank[];
  /**
   * @minItems 1
   */
  pipes: [Pipe, ...Pipe[]];
  valves: Valve[];
  /**
   * @minItems 1
   */
  fixtures: [Fixture, ...Fixture[]];
}
/**
 * This interface was referenced by `House`'s JSON-Schema
 * via the `definition` "WaterMeter".
 */
export interface WaterMeter {
  id: ObjectId;
  name: string;
  room_id: ObjectId;
  /**
   * Where the meter sits relative to the utility connection.
   */
  position: string;
}
/**
 * This interface was referenced by `House`'s JSON-Schema
 * via the `definition` "Tank".
 */
export interface Tank {
  id: ObjectId;
  name: string;
  room_id: ObjectId;
  capacity_l: number;
  inlet_pipe_id: ObjectId;
  material?: string;
  source_ref?: SourceRef;
}
/**
 * Pipe segment. upstream_id is the meter, a tank or another pipe.
 *
 * This interface was referenced by `House`'s JSON-Schema
 * via the `definition` "Pipe".
 */
export interface Pipe {
  id: ObjectId;
  name: string;
  kind: PipeKind;
  upstream_id: ObjectId;
  routing: PipeRouting;
  room_ids: ObjectId[];
  /**
   * Walls, ceilings or ducts that hide this segment.
   */
  concealed_in: ObjectId[];
  nominal_size_mm?: number;
}
/**
 * This interface was referenced by `House`'s JSON-Schema
 * via the `definition` "Valve".
 */
export interface Valve {
  id: ObjectId;
  name: string;
  kind: ValveKind;
  pipe_id: ObjectId1;
  room_id: ObjectId;
  /**
   * Whether an owner can safely operate it without tools or roof access (used by guided checks).
   */
  owner_operable: boolean;
}
/**
 * This interface was referenced by `House`'s JSON-Schema
 * via the `definition` "Fixture".
 */
export interface Fixture {
  id: ObjectId;
  name: string;
  kind: FixtureKind;
  room_id: ObjectId;
  pipe_id: ObjectId;
  isolation_valve_id?: ObjectId;
  install_date: CalendarDate;
}
/**
 * This interface was referenced by `House`'s JSON-Schema
 * via the `definition` "ElectricalSystem".
 */
export interface ElectricalSystem {
  supply: Supply;
  /**
   * @minItems 1
   */
  boards: [Board, ...Board[]];
  /**
   * @minItems 1
   */
  protective_devices: [ProtectiveDevice, ...ProtectiveDevice[]];
  /**
   * @minItems 1
   */
  circuits: [Circuit, ...Circuit[]];
  socket_groups: SocketGroup[];
}
/**
 * This interface was referenced by `House`'s JSON-Schema
 * via the `definition` "Supply".
 */
export interface Supply {
  phases: SupplyPhases;
  nominal_voltage_v: number;
  frequency_hz: number;
}
/**
 * This interface was referenced by `House`'s JSON-Schema
 * via the `definition` "Board".
 */
export interface Board {
  id: ObjectId;
  name: string;
  room_id: ObjectId;
}
export interface ProtectiveDevice1 {
  id: ObjectId;
  name: string;
  kind: ProtectiveDeviceKind;
  board_id: ObjectId;
  upstream_device_id: NullableObjectId;
  rated_current_a: number;
  /**
   * Rated residual operating current IΔn (RCCB only).
   */
  rated_residual_ma?: number;
  curve?: TripCurve;
  breaking_capacity_ka?: number;
  poles: Poles;
  install_date: CalendarDate;
}
/**
 * This interface was referenced by `House`'s JSON-Schema
 * via the `definition` "Circuit".
 */
export interface Circuit {
  id: ObjectId;
  name: string;
  kind: CircuitKind;
  mcb_id: ObjectId;
  cable_csa_mm2: number;
  /**
   * @minItems 1
   */
  serves_room_ids: [ObjectId, ...ObjectId[]];
}
/**
 * This interface was referenced by `House`'s JSON-Schema
 * via the `definition` "SocketGroup".
 */
export interface SocketGroup {
  id: ObjectId;
  name: string;
  room_id: ObjectId;
  circuit_id: ObjectId;
  outlet_count: number;
  outdoor: boolean;
}
/**
 * Electric appliances name exactly one of circuit_id (dedicated circuit) or socket_group_id; gas items name neither.
 *
 * This interface was referenced by `House`'s JSON-Schema
 * via the `definition` "Appliance".
 */
export interface Appliance {
  id: ObjectId;
  name: string;
  kind: ApplianceKind;
  room_id: ObjectId;
  circuit_id?: ObjectId;
  socket_group_id?: ObjectId;
  fixture_id?: ObjectId2;
  install_date: CalendarDate1;
  nameplate?: Nameplate;
}
/**
 * Representative nameplate data. Only values with a cited source are recorded.
 *
 * This interface was referenced by `House`'s JSON-Schema
 * via the `definition` "Nameplate".
 */
export interface Nameplate {
  rated_input_w?: number;
  /**
   * Voltage at which rated_input_w is stated (a resistive load draws (V/rated_voltage_v)^2 of it).
   */
  rated_voltage_v?: number;
  nominal_hp?: number;
  cooling_capacity_btu_h?: number;
  inverter?: boolean;
  source_ref: SourceRef;
}
export interface Sensor1 {
  id: ObjectId;
  name: string;
  kind: SensorKind;
  mounted_on_id: NullableObjectId1;
  spec: {
    [k: string]: unknown;
  };
}
/**
 * Metrology per OIML R49 / ISO 4064 terms. Q1 = Q3 / ratio_r.
 *
 * This interface was referenced by `House`'s JSON-Schema
 * via the `definition` "WaterMeterSpec".
 */
export interface WaterMeterSpec {
  technology: MeterTechnology;
  nominal_diameter_mm: number;
  q3_m3_per_h: number;
  ratio_r: number;
  accuracy_class: MeterAccuracyClass;
  scale_interval_l: number;
  source_ref: SourceRef;
}
/**
 * This interface was referenced by `House`'s JSON-Schema
 * via the `definition` "PressureSensorSpec".
 */
export interface PressureSensorSpec {
  range_max_bar: number;
  accuracy_pct_full_scale: number;
  resolution_bar: number;
  basis: SpecBasis;
}
/**
 * This interface was referenced by `House`'s JSON-Schema
 * via the `definition` "TankLevelSensorSpec".
 */
export interface TankLevelSensorSpec {
  technology: LevelTechnology;
  accuracy_mm: number;
  resolution_pct: number;
  basis: SpecBasis;
}
/**
 * Distribution-board monitor: a CT per final circuit and on the main incomer, voltage at the incomer, a residual current monitor per RCCB group, and auxiliary contacts on every protective device.
 *
 * This interface was referenced by `House`'s JSON-Schema
 * via the `definition` "DbMonitorSpec".
 */
export interface DbMonitorSpec {
  current_accuracy_pct: number;
  current_resolution_a: number;
  residual_resolution_ma: number;
  residual_range_max_ma: number;
  voltage_resolution_v: number;
  power_resolution_w: number;
  energy_resolution_wh: number;
  /**
   * Auxiliary/fault-signal contacts report tripped/reset state per device. They do not report the cause of a trip.
   */
  aux_contacts: boolean;
  basis: SpecBasis;
}
/**
 * This interface was referenced by `House`'s JSON-Schema
 * via the `definition` "SmartPlugSpec".
 */
export interface SmartPlugSpec {
  max_load_a: number;
  power_accuracy_pct: number;
  power_resolution_w: number;
  basis: SpecBasis;
}
/**
 * This interface was referenced by `House`'s JSON-Schema
 * via the `definition` "WeatherFeedSpec".
 */
export interface WeatherFeedSpec {
  provider: string;
  source_ref: SourceRef;
}
