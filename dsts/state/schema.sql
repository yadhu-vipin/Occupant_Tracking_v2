CREATE TABLE registered_state (
    time TEXT,
    occupant TEXT,
    zone TEXT,
    probability REAL,
    PRIMARY KEY (time, occupant, zone)
);

CREATE TABLE visitor_state (
    time TEXT,
    occupant TEXT,
    zone TEXT,
    probability REAL,
    PRIMARY KEY (time, occupant, zone)
);

-- Visit pointers: when a visitor is confirmed at a non-home building, a
-- pointer (visit_id + current location) is anchored to the transition-zone
-- (zT) entry/exit events so queries can redirect straight to the visitor's
-- current building instead of broadcasting to every building. Carries only
-- occupant_id + location + timestamps -- no embeddings, no votes, no
-- reference vectors.
CREATE TABLE visitor_pointers (
    visit_id          TEXT PRIMARY KEY,
    occupant_id       TEXT NOT NULL,
    current_building  TEXT NOT NULL,
    entry_time        TEXT NOT NULL,
    exit_time         TEXT,          -- NULL while open
    status            TEXT NOT NULL  -- 'OPEN' | 'CLOSED'
);

CREATE INDEX idx_pointers_occupant_open ON visitor_pointers(occupant_id, status);
