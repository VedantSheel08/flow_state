-- FlowState experiment store: PostgreSQL (TimescaleDB hypertables are created only when the extension exists).
CREATE TABLE IF NOT EXISTS experiments (
  id uuid NOT NULL,
  name text NOT NULL,
  scenario text NOT NULL,
  controller text NOT NULL,
  network text NOT NULL,
  seed integer NOT NULL,
  params jsonb NOT NULL,
  params_hash text NOT NULL,
  warmup_s double precision NOT NULL,
  duration_s double precision NOT NULL,
  started_at timestamptz NOT NULL,
  finished_at timestamptz,
  status text NOT NULL,
  baseline_id uuid,
  suite text,
  tags text[],
  engine jsonb NOT NULL,
  emission_model text,
  notes text,
  error text,
  PRIMARY KEY (id),
  FOREIGN KEY (baseline_id) REFERENCES experiments(id)
);

CREATE INDEX IF NOT EXISTS experiments_scenario_params_hash_seed_controller_idx ON experiments (scenario, params_hash, seed, controller);

CREATE INDEX IF NOT EXISTS experiments_suite_status_idx ON experiments (suite, status);

CREATE INDEX IF NOT EXISTS experiments_started_at_idx ON experiments (started_at);

CREATE INDEX IF NOT EXISTS experiments_controller_scenario_idx ON experiments (controller, scenario);

CREATE TABLE IF NOT EXISTS kpi_samples (
  experiment_id uuid NOT NULL,
  ts timestamptz NOT NULL,
  sim_t double precision NOT NULL,
  active_vehicles integer,
  halted_vehicles integer,
  mean_speed_kmh double precision,
  density_veh_km_lane double precision,
  storage_occupancy double precision,
  queue_total integer,
  queue_max integer,
  queued_junctions integer,
  pending integer,
  persons integer,
  completed_total integer,
  throughput_per_h double precision,
  mean_travel_s double precision,
  mean_wait_s double precision,
  mean_time_loss_s double precision,
  co2_mg_s double precision,
  co_mg_s double precision,
  hc_mg_s double precision,
  nox_mg_s double precision,
  pmx_mg_s double precision,
  fuel_mg_s double precision,
  idling_vehicles integer,
  idle_co2_mg_s double precision,
  idle_fuel_mg_s double precision,
  teleports_total integer,
  collisions_total integer,
  emergency_stops_total integer,
  PRIMARY KEY (experiment_id, ts),
  FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS kpi_samples_experiment_id_sim_t_idx ON kpi_samples (experiment_id, sim_t);

CREATE TABLE IF NOT EXISTS edge_samples (
  experiment_id uuid NOT NULL,
  ts timestamptz NOT NULL,
  sim_t double precision NOT NULL,
  edge_id text NOT NULL,
  vehicles integer NOT NULL,
  halting integer,
  mean_speed_ms double precision,
  occupancy double precision,
  co2_mg_s double precision,
  fuel_mg_s double precision,
  PRIMARY KEY (experiment_id, ts, edge_id),
  FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS edge_samples_experiment_id_edge_id_sim_t_idx ON edge_samples (experiment_id, edge_id, sim_t);

CREATE TABLE IF NOT EXISTS junction_samples (
  experiment_id uuid NOT NULL,
  ts timestamptz NOT NULL,
  sim_t double precision NOT NULL,
  tls_id text NOT NULL,
  queue integer NOT NULL,
  crossings integer,
  phase integer,
  PRIMARY KEY (experiment_id, ts, tls_id),
  FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS junction_samples_experiment_id_tls_id_sim_t_idx ON junction_samples (experiment_id, tls_id, sim_t);

CREATE TABLE IF NOT EXISTS signal_events (
  experiment_id uuid NOT NULL,
  ts timestamptz NOT NULL,
  sim_t double precision NOT NULL,
  tls_id text NOT NULL,
  kind text NOT NULL,
  phase_from integer,
  phase_to integer,
  elapsed_s double precision,
  state text,
  detail jsonb,
  FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS signal_events_experiment_id_sim_t_idx ON signal_events (experiment_id, sim_t);

CREATE INDEX IF NOT EXISTS signal_events_experiment_id_tls_id_sim_t_idx ON signal_events (experiment_id, tls_id, sim_t);

CREATE INDEX IF NOT EXISTS signal_events_experiment_id_kind_idx ON signal_events (experiment_id, kind);

CREATE TABLE IF NOT EXISTS safety_events (
  experiment_id uuid NOT NULL,
  ts timestamptz NOT NULL,
  sim_t double precision NOT NULL,
  kind text NOT NULL,
  subtype text,
  vehicle text,
  other text,
  lane text,
  speed_ms double precision,
  other_speed_ms double precision,
  detail jsonb,
  FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS safety_events_experiment_id_kind_sim_t_idx ON safety_events (experiment_id, kind, sim_t);

CREATE TABLE IF NOT EXISTS trip_results (
  experiment_id uuid NOT NULL,
  vehicle_id text NOT NULL,
  mode text,
  depart_s double precision,
  arrival_s double precision,
  finished boolean,
  route_length_m double precision,
  duration_s double precision,
  waiting_s double precision,
  time_loss_s double precision,
  stops integer,
  co2_mg double precision,
  fuel_mg double precision,
  nox_mg double precision,
  pmx_mg double precision,
  PRIMARY KEY (experiment_id, vehicle_id),
  FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS trip_results_experiment_id_mode_idx ON trip_results (experiment_id, mode);

CREATE TABLE IF NOT EXISTS experiment_summary (
  experiment_id uuid NOT NULL,
  trips_completed integer,
  throughput_per_h double precision,
  mean_travel_s double precision,
  mean_time_loss_s double precision,
  mean_wait_s double precision,
  stops_per_trip double precision,
  mean_speed_kmh double precision,
  mean_density_veh_km_lane double precision,
  mean_storage_occupancy double precision,
  peak_storage_occupancy double precision,
  mean_queue double precision,
  peak_queue integer,
  pending_max integer,
  teleports integer,
  collisions integer,
  emergency_stops integer,
  co2_total_g double precision,
  co2_g_per_km double precision,
  co2_g_per_trip double precision,
  fuel_total_g double precision,
  idle_s_total double precision,
  idle_co2_g double precision,
  idle_fuel_g double precision,
  idle_share_of_co2 double precision,
  extra jsonb,
  PRIMARY KEY (experiment_id),
  FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE CASCADE
);
