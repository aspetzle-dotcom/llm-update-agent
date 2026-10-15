-- Forecasting dashboard projects: user-supplied series plus the settings and summary
-- of the latest forecast. Independent of collected evidence; users may delete these.
CREATE TABLE forecast_projects (
 project_id TEXT PRIMARY KEY,
 name TEXT NOT NULL,
 data_json TEXT NOT NULL,
 settings_json TEXT NOT NULL,
 summary_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
