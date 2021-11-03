CREATE TABLE install_packages (
    id SERIAL PRIMARY KEY,
    itsm_id VARCHAR(64) NOT NULL,
    minion_id VARCHAR(64) NOT NULL,
    package_name VARCHAR(128) NOT NULL,
    package_version VARCHAR(128),
    after TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW() NOT NULL,
    reverted BOOLEAN DEFAULT FALSE NOT NULL
);

CREATE INDEX install_packages_itsm_id_idx ON install_packages (itsm_id);
CREATE INDEX install_packages_minion_id_idx ON install_packages (minion_id);
CREATE INDEX install_packages_package_name_idx ON install_packages (minion_id, package_name);
CREATE INDEX install_packages_reverted_idx ON install_packages (reverted);
CREATE UNIQUE INDEX install_packages_itsm_unique_idx ON install_packages (itsm_id, minion_id, package_name, package_version);
