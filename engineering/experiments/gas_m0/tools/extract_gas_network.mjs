/** Offline extraction of a pinned, reviewed construction block, never the UI. */
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const base = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const project = path.resolve(base, '..', '..');
const options = {};
for (let i = 2; i < process.argv.length; i += 2) {
  const key = process.argv[i], value = process.argv[i + 1];
  if (!['--source', '--output-root'].includes(key) || !value || value.startsWith('--') || key in options) {
    throw new Error('Usage: node extract_gas_network.mjs [--source FILE] [--output-root NEW_DIRECTORY]');
  }
  options[key] = value;
}
const source = options['--source'] ? path.resolve(options['--source']) : path.join(project, '燃气动态风险推演_前端示例原型_v1.html');
const outputRoot = options['--output-root'] ? path.resolve(options['--output-root']) : base;
const expectedHtml = '21671fbdd368a4fb6e8757f18af91e5aa69a0221098b022c00009a4bf4a88832';
const expectedBlock = 'e354dba828fc226d64ee7b848b162cd3712a783952f447aec075b81b013baaf5';
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const bytes = fs.readFileSync(source);
if (hash(bytes) !== expectedHtml) throw new Error('Original HTML hash changed; re-review extraction first.');
if (process.version !== 'v24.14.1' || process.versions.v8 !== '13.6.233.17-node.44') {
  throw new Error('Pinned extraction requires Node v24.14.1 / V8 13.6.233.17-node.44: random sort comparator is engine-sensitive.');
}
const html = bytes.toString('utf8');
const start = html.indexOf('function mulberry32');
const end = html.indexOf('/* ---------- 链 C：PIR ---------- */');
if (start < 0 || end <= start) throw new Error('Reviewed source markers missing.');
const block = html.slice(start, end);
if (hash(block) !== expectedBlock) throw new Error('Reviewed construction block hash mismatch.');
// No require, process, fetch, timers, filesystem, DOM, or host callback is exposed.
// A hash change stops execution before vm. vm alone is not a general security boundary.
const context = vm.createContext(Object.create(null), { codeGeneration: { strings: false, wasm: false } });
vm.runInContext(block, context, { timeout: 1500, filename: 'pinned-gas-construction.js' });
const raw = JSON.parse(vm.runInContext(`JSON.stringify({
  nodes:nodes.map(n=>({id:n.id,r:n.r,c:n.c,x:n.x,y:n.y,type:n.type,name:n.name||null,hh:n.hh,com:n.com})),
  edges:edges.map(e=>({...e})),
  facilities:facs.map((f,i)=>({id:'FAC-'+String(i+1).padStart(2,'0'),type:f.t,weight:f.w,node_id:f.n.id,x:f.x,y:f.y})),
  spaces:spaces, construction_sites:cons,
  units:Object.keys(units).map(u=>({unit_id:Number(u),edge_ids:units[u].map(i=>edges[i].id),valve_node_ids:unitValves(u).map(i=>nodes[i].id)})),
  contexts:CTXNAME, initial_context:state.ctx, risk_matrix:MATRIX,
  context_factors:KAPPA, objective_names:DIMS, initial_subjective_weights:state.ahp,
  prototype_data_available_flags:state.data, declared_skills:SKILLS,
  prototype_cases:edges.map((e,i)=>({fault_pipe_id:e.id,unit_id:e.unit,
    disconnected_consumer_node_ids:disconnected(units[e.unit],unitValves(e.unit)).map(j=>nodes[j].id),
    base_users:((u)=>({households:u.hh,businesses:u.com,facility_node_ids:u.fac.map(f=>f.n.id)}))(usersOf(disconnected(units[e.unit],unitValves(e.unit))))}))
})`, context, { timeout: 1500 }));

const id = (kind, original) => `syn-gas-${kind}-${original}`;
const networkId = 'syn-gas-network-html54-0.1.0';
const nodes = raw.nodes.map(n => ({
  node_id: id('node', n.id), prototype_id: n.id, node_type: n.type,
  name: n.name, layout: { x: n.x, y: n.y, unit: 'px', coordinate_system: 'schematic' },
}));
const edges = raw.edges.map(e => ({
  edge_id: id('edge', e.id), pipe_id: id('pipe', e.id), prototype_id: e.id,
  from_node_id: id('node', e.a), to_node_id: id('node', e.b),
  direction: 'undirected_connectivity_only', initial_state: 'enabled',
}));
const pipes = raw.edges.map(e => ({
  pipe_id: id('pipe', e.id), edge_id: id('edge', e.id), prototype_id: e.id,
  material: e.mat, diameter_mm: e.D, pressure_class: e.plv,
  asset_status: 'synthetic_not_business_validated',
}));
const valves = raw.nodes.filter(n => n.type === 'valve').map(n => ({
  valve_id: id('valve', n.id), node_id: id('node', n.id), prototype_id: n.id,
  cut_semantics: 'node_cut', initial_state: 'open',
  incident_edge_ids: raw.edges.filter(e => e.a === n.id || e.b === n.id).map(e => id('edge', e.id)),
}));
const sources = raw.nodes.filter(n => n.type === 'source').map(n => ({
  source_id: id('source', n.id), node_id: id('node', n.id), prototype_id: n.id,
  name: n.name, enabled: true, pressure: null, capacity: null,
  physical_model_status: 'unsupported',
}));
const consumers = [], services = [];
for (const n of raw.nodes) {
  for (const [kind, count, unit] of [['household', n.hh, 'household'], ['business', n.com, 'business']]) {
    if (count === 0) continue;
    const original = `${kind}-${n.id}`;
    consumers.push({ consumer_id: id('consumer', original), prototype_node_id: n.id,
      node_id: id('node', n.id), consumer_kind: kind, count, count_unit: unit,
      origin: 'prototype_node_aggregate', individual_customer_records: false });
    services.push({ service_id: id('service', original), consumer_id: id('consumer', original),
      service_kind: kind, node_ids: [id('node', n.id)], supply_rule: 'any',
      connection_origin: 'explicit_prototype_node_assignment', requires_physical_validation: true });
  }
}
for (const f of raw.facilities) {
  consumers.push({ consumer_id: id('consumer', f.id), prototype_node_id: f.node_id,
    node_id: id('node', f.node_id), consumer_kind: 'facility', count: 1, count_unit: 'facility',
    facility_type: f.type, origin: 'prototype_facility_node_reference', individual_customer_records: false });
  services.push({ service_id: id('service', f.id), consumer_id: id('consumer', f.id),
    service_kind: 'facility', node_ids: [id('node', f.node_id)], supply_rule: 'any',
    connection_origin: 'explicit_prototype_facility_node_reference', requires_physical_validation: true });
}
const network = {
  schema_version: 'gas-network-candidate-0.1.0', network_id: networkId,
  data_mode: 'synthetic', industry: 'gas', validation_status: 'not_business_validated',
  model_scope: 'graph_connectivity_only', coordinate_system: 'schematic',
  source_provenance: { file: path.basename(source), sha256: expectedHtml,
    construction_block_sha256: expectedBlock, first_line: html.slice(0, start).split('\n').length,
    last_line: html.slice(0, end).split('\n').length - 1, seed: 20261104,
    node_version: process.version, v8_version: process.versions.v8,
    reproducibility_limit: 'The original sort comparator consumes random numbers and is not a total order. Re-extract only with the pinned runtime; the committed JSON is the portable canonical fixture.',
    connection_evidence: 'edges[].a/b and facs[].n / nodes[].hh/com from reviewed source; never inferred from SVG or distances.',
  },
  nodes, edges, pipes, valves, sources, consumers, services,
  isolation_units: raw.units.map(u => ({ unit_id: id('unit', String(u.unit_id)),
    prototype_unit_id: u.unit_id, edge_ids: u.edge_ids.map(x => id('edge', x)),
    valve_ids: u.valve_node_ids.map(x => id('valve', x)),
    origin: 'prototype_union_of_incident_edges_at_non_valve_nodes',
  })),
  prototype_contexts: Object.entries(raw.contexts).map(([key, name]) => ({ context_id: key, name })),
  baseline: { disabled_edge_ids: [], closed_valve_ids: [], disabled_source_ids: [] },
  extraction_notes: [
    'Edge endpoint order is storage order, not upstream/downstream or simulated gas flow direction.',
    'Valves are node cuts. A valve is not a pipe or edge; incident_edge_ids is an explicit incidence list, not one-to-one control ownership.',
    'Facilities at N24 and N45 are retained, although the prototype disconnected() excludes all valve/source nodes from user accounting. The candidate exposes them rather than silently discarding them.',
    'Pressure, flow, leakage, dispersion, ignition, explosion, field valve instruction and safe gas restoration are unsupported.',
  ],
  demo_parameters: {
    use: 'source_preservation_only_not_executed_by_candidate_topology',
    origin: 'hardcoded_or_seeded_prototype_values_not_measurements_or_predictions',
    coordinates_to_length: { pixels_per_demo_meter: 0.5, actual_gis: false },
    isolation: { default_valve_failure_fraction: 0.15, enabled_flag_fraction: 0.10,
      assumed_outage_hours_main: 8, assumed_outage_hours_other: 5,
      assumed_repair_cost_main: 80000, assumed_repair_cost_other: 30000,
      household_time_coefficient: 0.05, business_time_coefficient: 2,
      business_public_weight: 1.5, economic_time_multiplier: 3,
      field_operation_authorized: false },
    consequences: { pir_coefficient: 0.0997, minimum_radius_main_m: 20,
      minimum_radius_other_m: 10, accumulation_decay_length_m: 6,
      assumed_depth_difference_m: 1.2, ignition_fraction: 0.08,
      space_volume_radius_pairs: [[50,15],[500,30],[null,50]], model_validation: 'not_business_validated' },
    evolution: { transition_base_rates: [0.010,0.012,0.015,0.018],
      intervention_lower_state_fraction: 0.8, intervention_stay_fraction: 0.2,
      number_of_steps: 12, representative_state_scores: [10,30,50,70,90],
      assumed_failure_intensity_base: 0.05, score_doubling_interval: 10,
      construction_exponent_coefficient: 0.8, seasonal_exponent_coefficient: 0.3,
      source_only: true, probability_calibration: 'absent',
      source_expression: 'p12 = 1-exp(-lambda*e.Lkm); label and preset intensity do not establish a calibrated prediction horizon' },
    scoring_and_values: { source_thresholds_less_than: [20,40,60,80],
      risk_matrix: raw.risk_matrix, initial_subjective_weights: raw.initial_subjective_weights,
      context_factors: raw.context_factors, objective_names: raw.objective_names,
      subjective_fraction: 0.6, entropy_fraction: 0.4, aggregation_power: 4,
      risk_normalization_percentile: 0.95,
      professional_g01_g15_scoring: false },
    spatial_and_hotspots: { canvas_cell_size_px: 125, cell_columns: 8, cell_rows: 5,
      neighbor_distance_px: 375, fdr_alpha: 0.05, z_thresholds: [1.96,2.58],
      cell_length_weight: 0.7, cell_p95_weight: 0.3,
      population_density_base: 8000, population_density_amplitude: 14000,
      population_center_px: [520,320], population_spread_px: 260,
      soil_region_x_boundaries_px: [350,700], soil_multipliers: [1.4,1.0,0.7],
      accumulation_horizontal_cutoff_m: 30, crossing_distance_m: 1, crossing_multiplier: 1.2,
      actual_population_or_gis_records: false, professional_significance_validation: false },
    source_only_formulas: {
      generated_P: 'clamp(round(16 + 55*min(age/design,1.2) + uniform(-8,14) + 12*construction_exposure),8,94)',
      generated_C: 'clamp(round(34 + 34*density_fraction + (main?16:0) + uniform(-6,8)),15,96)',
      original_R: 'sqrt(P*Cg)',
      original_system_R: 'sqrt(P*min(100,Cg*CAF))',
      original_final_grade: 'max(levelOf(R), MATRIX[levelOf(P)-1][levelOf(Cg)-1])',
      original_system_redline: 'max(original_final_grade,levelOf(system_R),base_condition?4:0)',
      original_evidence_interval: '[E*P, E*P+(1-E)*100]',
      original_VOI: 'If interval endpoint grades differ, sqrt(upper*Csys)-sqrt(lower*Csys); otherwise 0.',
      use: 'Preserved labels and arithmetic assumptions only; none evaluated by the gas graph candidate.',
    },
    skill_declarations: raw.declared_skills,
    prototype_data_available_flags: { values: raw.prototype_data_available_flags,
      meaning: 'UI toggles only, not evidence that any real dataset or professional model was received.' },
    per_pipe: raw.edges.map(e => ({ pipe_id: id('pipe', e.id), prototype_id: e.id,
      assumed_pressure_mpa: e.press, schematic_length_m: e.Lm,
      assumed_design_life_years: e.design, generated_age_years: e.age,
      generated_P_score: e.P, generated_C_score: e.Cg, generated_E_fraction: e.E,
      generated_aging_factor: e.af, generated_construction_exposure: e.zc,
      assumed_intervention_period: e.tint })),
    facilities: raw.facilities.map(f => ({ consumer_id: id('consumer', f.id),
      assumed_importance_weight: f.weight, display_x: f.x, display_y: f.y })),
    underground_spaces: raw.spaces,
    construction_sites: raw.construction_sites,
  },
};
const chosen = raw.prototype_cases.find(c => c.fault_pipe_id === 'K-001');
const unit = network.isolation_units.find(u => u.prototype_unit_id === chosen.unit_id);
const faultEdge = id('edge', 'K-001'), scenarioId = 'syn-gas-scenario-K-001';
const scenario = {
  schema_version: 'gas-case-candidate-0.1.0', case_id: 'syn-gas-case-K-001', network_id: networkId,
  data_mode: 'synthetic', model_status: 'candidate', analysis_window_hours: 8,
  scenario: { scenario_id: scenarioId, name: '合成K-001故障及原型隔离单元通路影响',
    fault_edge_ids: [faultEdge], origin: 'new_synthetic_case_on_extracted_network',
    fault_assumption: 'Given unavailable edge, not a predicted leak or an incident record.' },
  isolation_plan: { plan_id: 'syn-gas-isolation-unit-16', scenario_id: scenarioId,
    closed_valve_ids: unit.valve_ids, disabled_edge_ids: unit.edge_ids,
    semantics: 'node_cut_plus_explicit_unit_edges',
    derivation: 'Prototype unitValves and units for K-001; explicitly given educational candidate, no real operation authorization.' },
  events: [
    { event_id: 'syn-gas-event-fault', time_hours: 0, kind: 'given_fault',
      origin: 'new_synthetic_case', fault_edge_ids: [faultEdge], closed_valve_ids: [], disabled_edge_ids: [] },
    { event_id: 'syn-gas-event-isolation', time_hours: 0.5, kind: 'given_isolation',
      origin: 'new_synthetic_case', fault_edge_ids: [faultEdge], closed_valve_ids: unit.valve_ids, disabled_edge_ids: unit.edge_ids },
    { event_id: 'syn-gas-event-observation', time_hours: 1, kind: 'synthetic_observation',
      origin: 'new_synthetic_case', fault_edge_ids: [faultEdge], closed_valve_ids: unit.valve_ids, disabled_edge_ids: unit.edge_ids },
  ],
  task_end_hours: null,
  missing: ['Actual isolation task end', 'Repair, inspection, purge, release and physical gas restoration records'],
  expected_from_source: {
    basis: 'Independent expectation: original disconnected/units/unitValves topology, excluding prototype valve/source nodes; no expected fractional failure calculation.',
    disconnected_consumer_node_ids: chosen.disconnected_consumer_node_ids.map(x => id('node', x)),
    households: chosen.base_users.households, businesses: chosen.base_users.businesses,
    facility_node_ids: chosen.base_users.facility_node_ids.map(x => id('node', x)),
  },
  safety: { field_action_instruction: false, safe_restoration_decision: false,
    physical_supply_verified: false, simulation_only: true },
};
const allProbes = raw.prototype_cases.map(c => ({
  fault_edge_id: id('edge', c.fault_pipe_id), unit_id: id('unit', String(c.unit_id)),
  disconnected_consumer_node_ids: c.disconnected_consumer_node_ids.map(x => id('node', x)),
  household_count: c.base_users.households, business_count: c.base_users.businesses,
  facility_node_ids: c.base_users.facility_node_ids.map(x => id('node', x)),
}));
const output = [
  ['data/gas54_network.json', network], ['data/gas54_case.json', scenario],
  ['evidence/network/prototype_topology_expectations.json', { source_sha256: expectedHtml,
    construction_block_sha256: expectedBlock, node_version: process.version, cases: allProbes }],
];
// Reject before any output write, rather than silently replacing a reviewed fixture.
for (const [relative] of output) {
  if (fs.existsSync(path.join(outputRoot, relative))) {
    throw new Error(`Output exists: ${relative}. Use --output-root with a new isolated directory and compare hashes.`);
  }
}
for (const [relative, value] of output) {
  const dest = path.join(outputRoot, relative);
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.writeFileSync(dest, JSON.stringify(value, null, 2) + '\n', 'utf8');
}
console.log(JSON.stringify({ files: output.map(([relative]) => ({ path: relative, sha256: hash(fs.readFileSync(path.join(outputRoot, relative))) })),
  counts: Object.fromEntries(['nodes','edges','pipes','valves','sources','consumers','services','isolation_units'].map(k => [k,network[k].length])),
  expected_case: scenario.expected_from_source,
}, null, 2));
