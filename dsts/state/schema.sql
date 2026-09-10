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
