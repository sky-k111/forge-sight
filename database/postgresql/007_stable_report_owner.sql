-- Normalize legacy username ownership to immutable platform_user ids.
-- Before this migration authenticated report APIs persisted usernames.

CREATE TABLE IF NOT EXISTS forge_sight_data_migration (
    id VARCHAR(80) PRIMARY KEY,
    applied_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

WITH run_once AS (
    INSERT INTO forge_sight_data_migration (id)
    VALUES ('007-stable-report-owner')
    ON CONFLICT (id) DO NOTHING
    RETURNING id
)
UPDATE report AS report_row
SET owner_id = platform_user.id
FROM platform_user, run_once
WHERE report_row.owner_id = platform_user.username;

WITH run_once AS (
    INSERT INTO forge_sight_data_migration (id)
    VALUES ('007-stable-subscription-owner')
    ON CONFLICT (id) DO NOTHING
    RETURNING id
)
UPDATE report_subscription AS subscription_row
SET owner_id = platform_user.id
FROM platform_user, run_once
WHERE subscription_row.owner_id = platform_user.username;
