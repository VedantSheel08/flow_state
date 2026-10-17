-- FlowState experiment store: SQLite fallback with the same columns (ts is unix seconds, json/arrays are TEXT).
CREATE TABLE IF NOT EXISTS experiments (
  id TEXT NOT NULL,
  name TEXT NOT NULL,
  scenario TEXT NOT NULL,
  controller TEXT NOT NULL,
  network TEXT NOT NULL,
  seed INTEGER NOT NULL,
  params TEXT NOT NULL,
  params_hash TEXT NOT NULL,
  warmup_s REAL NOT NULL,
  duration_s REAL NOT NULL,
  started_at REAL NOT NULL,
  finished_at REAL,
  status TEXT NOT NULL,
  baseline_id TEXT,
  suite TEXT,
  tags TEXT,
  engine TEXT NOT NULL,
  emission_model TEXT,
  notes TEXT,
  error TEXT,
  PRIMARY KEY (id),
  FOREIGN KEY (baseline_id) REFERENCES experiments(id)
);

CREATE INDEX IF NOT EXISTS experiments_scenario_params_hash_seed_controller_idx ON experiments (scenario, params_hash, seed, controller);

CREATE INDEX IF NOT EXISTS experiments_suite_status_idx ON experiments (suite, status);

CREATE INDEX IF NOT EXISTS experiments_started_at_idx ON experiments (started_at);

CREATE INDEX IF NOT EXISTS experiments_controller_scenario_idx ON experiments (controller, scenario);

CREATE TABLE IF NOT EXISTS kpi_samples (
  experiment_id TEXT NOT NULL,
  ts REAL NOT NULL,
  sim_t REAL NOT NULL,
  active_vehicles INTEGER,
  halted_vehicles INTEGER,
  mean_speed_kmh REAL,
  density_veh_km_lane REAL,
  storage_occupancy REAL,
  queue_total INTEGER,
  queue_max INTEGER,
  queued_junctions INTEGER,
  pending INTEGER,
  persons INTEGER,
  completed_total INTEGER,
  throughput_per_h REAL,
  mean_travel_s REAL,
  mean_wait_s REAL,
  mean_time_loss_s REAL,
  co2_mg_s REAL,
  co_mg_s REAL,
  hc_mg_s REAL,
  nox_mg_s REAL,
  pmx_mg_s REAL,
  fuel_mg_s REAL,
  idling_vehicles INTEGER,
  idle_co2_mg_s REAL,
  idle_fuel_mg_s REAL,
  teleports_total INTEGER,
  collisions_total INTEGER,
  emergency_stops_total INTEGER,
  PRIMARY KEY (experiment_id, ts),
  FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS kpi_samples_experiment_id_sim_t_idx ON kpi_samples (experiment_id, sim_t);

CREATE TABLE IF NOT EXISTS edge_samples (
  experiment_id TEXT NOT NULL,
  ts REAL NOT NULL,
  sim_t REAL NOT NULL,
  edge_id TEXT NOT NULL,
  vehicles INTEGER NOT NULL,
  halting INTEGER,
  mean_speed_ms REAL,
  occupancy REAL,
  co2_mg_s REAL,
  fuel_mg_s REAL,
  PRIMARY KEY (experiment_id, ts, edge_id),
  FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS edge_samples_experiment_id_edge_id_sim_t_idx ON edge_samples (experiment_id, edge_id, sim_t);

CREATE TABLE IF NOT EXISTS junction_samples (
  experiment_id TEXT NOT NULL,
  ts REAL NOT NULL,
  sim_t REAL NOT NULL,
  tls_id TEXT NOT NULL,
  queue INTEGER NOT NULL,
  crossings INTEGER,
  phase INTEGER,
  PRIMARY KEY (experiment_id, ts, tls_id),
  FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS junction_samples_experiment_id_tls_id_sim_t_idx ON junction_samples (experiment_id, tls_id, sim_t);

CREATE TABLE IF NOT EXISTS signal_events (
  experiment_id TEXT NOT NULL,
  ts REAL NOT NULL,
  sim_t REAL NOT NULL,
  tls_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  phase_from INTEGER,
  phase_to INTEGER,
  elapsed_s REAL,
  state TEXT,
  detail TEXT,
  FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS signal_events_experiment_id_sim_t_idx ON signal_events (experiment_id, sim_t);

CREATE INDEX IF NOT EXISTS signal_events_experiment_id_tls_id_sim_t_idx ON signal_events (experiment_id, tls_id, sim_t);

CREATE INDEX IF NOT EXISTS signal_events_experiment_id_kind_idx ON signal_events (experiment_id, kind);

CREATE TABLE IF NOT EXISTS safety_events (
  experiment_id TEXT NOT NULL,
  ts REAL NOT NULL,
  sim_t REAL NOT NULL,
  kind TEXT NOT NULL,
  subtype TEXT,
  vehicle TEXT,
  other TEXT,
  lane TEXT,
  speed_ms REAL,
  other_speed_ms REAL,
  detail TEXT,
  FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS safety_events_experiment_id_kind_sim_t_idx ON safety_events (experiment_id, kind, sim_t);

CREATE TABLE IF NOT EXISTS trip_results (
  experiment_id TEXT NOT NULL,
  vehicle_id TEXT NOT NULL,
  mode TEXT,
  depart_s REAL,
  arrival_s REAL,
  finished INTEGER,
  route_length_m REAL,
  duration_s REAL,
  waiting_s REAL,
  time_loss_s REAL,
  stops INTEGER,
  co2_mg REAL,
  fuel_mg REAL,
  nox_mg REAL,
  pmx_mg REAL,
  PRIMARY KEY (experiment_id, vehicle_id),
  FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS trip_results_experiment_id_mode_idx ON trip_results (experiment_id, mode);

CREATE TABLE IF NOT EXISTS experiment_summary (
  experiment_id TEXT NOT NULL,
  trips_completed INTEGER,
  throughput_per_h REAL,
  mean_travel_s REAL,
  mean_time_loss_s REAL,
  mean_wait_s REAL,
  stops_per_trip REAL,
  mean_speed_kmh REAL,
  mean_density_veh_km_lane REAL,
  mean_storage_occupancy REAL,
  peak_storage_occupancy REAL,
  mean_queue REAL,
  peak_queue INTEGER,
  pending_max INTEGER,
  teleports INTEGER,
  collisions INTEGER,
  emergency_stops INTEGER,
  co2_total_g REAL,
  co2_g_per_km REAL,
  co2_g_per_trip REAL,
  fuel_total_g REAL,
  idle_s_total REAL,
  idle_co2_g REAL,
  idle_fuel_g REAL,
  idle_share_of_co2 REAL,
  extra TEXT,
  PRIMARY KEY (experiment_id),
  FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE CASCADE
);
