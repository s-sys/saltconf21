CREATE TABLE available_packages (
    id SERIAL PRIMARY KEY,
    operating_system VARCHAR(64) NOT NULL,
    package_name VARCHAR(128) NOT NULL,
    package_version VARCHAR(128)
);

CREATE INDEX available_packages_operating_system_idx ON available_packages (operating_system);
CREATE INDEX available_packages_package_name_idx ON available_packages (operating_system, package_name);
CREATE UNIQUE INDEX available_packages_package_version_idx ON available_packages (operating_system, package_name, package_version);
