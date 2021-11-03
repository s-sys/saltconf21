CREATE TABLE minions (
    minion_id VARCHAR(64) PRIMARY KEY,
    operating_system VARCHAR(64) NOT NULL,
    last_seen TIMESTAMP DEFAULT NOW() NOT NULL
);

CREATE INDEX minions_operating_system_idx ON minions (operating_system);
