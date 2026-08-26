CREATE TABLE registered_state (
    time TEXT,
    occupant TEXT,
    zone TEXT,
    probability REAL,
    PRIMARY KEY (time, occupant, zone)
);

CREATE TABLE registered_occupancy (
    start_time TEXT,
    occupant TEXT,
    zone TEXT,
    end_time TEXT,
    probability REAL,
    PRIMARY KEY (start_time, occupant, zone)
);

CREATE TABLE visitor_state (
    time TEXT,
    occupant TEXT,
    zone TEXT,
    probability REAL,
    PRIMARY KEY (time, occupant, zone)
);

CREATE TABLE visitor_occupancy (
    start_time TEXT,
    occupant TEXT,
    zone TEXT,
    end_time TEXT,
    probability REAL,
    PRIMARY KEY (start_time, occupant, zone)
);