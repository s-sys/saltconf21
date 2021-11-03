CREATE TABLE reboot_requests (
    id SERIAL PRIMARY KEY,
    itsm_id VARCHAR(64) NOT NULL,
    minion_id VARCHAR(64) NOT NULL,
    job_id VARCHAR(20) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW() NOT NULL
);

CREATE INDEX reboot_requests_itsm_id_idx ON reboot_requests (itsm_id);
CREATE INDEX reboot_requests_minion_id_idx ON reboot_requests (minion_id);
CREATE INDEX reboot_requests_job_id_idx ON reboot_requests (job_id);
