-- Incremental, non-destructive email identity migration.
-- Safe to execute repeatedly against both existing and freshly created databases.

ALTER TABLE platform_user
    ADD COLUMN IF NOT EXISTS email VARCHAR(254);
ALTER TABLE platform_user
    ADD COLUMN IF NOT EXISTS email_canonical VARCHAR(254);
ALTER TABLE platform_user
    ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMP WITHOUT TIME ZONE;

DO $migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_platform_user_email_pair'
          AND conrelid = 'platform_user'::regclass
    ) THEN
        ALTER TABLE platform_user
            ADD CONSTRAINT ck_platform_user_email_pair
            CHECK (
                (email IS NULL AND email_canonical IS NULL)
                OR (email IS NOT NULL AND email_canonical IS NOT NULL)
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_platform_user_verified_email'
          AND conrelid = 'platform_user'::regclass
    ) THEN
        ALTER TABLE platform_user
            ADD CONSTRAINT ck_platform_user_verified_email
            CHECK (email_verified_at IS NULL OR email IS NOT NULL);
    END IF;
END
$migration$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_platform_user_email
    ON platform_user (email_canonical)
    WHERE email_canonical IS NOT NULL;

CREATE TABLE IF NOT EXISTS email_verification_challenge (
    id VARCHAR(40) NOT NULL,
    user_id VARCHAR(40) NOT NULL,
    purpose VARCHAR(24) NOT NULL,
    email VARCHAR(254) NOT NULL,
    email_canonical VARCHAR(254) NOT NULL,
    code_hash VARCHAR(256),
    expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    attempt_count INTEGER DEFAULT 0 NOT NULL,
    send_count INTEGER DEFAULT 1 NOT NULL,
    last_sent_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    consumed_at TIMESTAMP WITHOUT TIME ZONE,
    superseded_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    CONSTRAINT pk_email_verification_challenge PRIMARY KEY (id),
    CONSTRAINT fk_email_verification_challenge_user
        FOREIGN KEY (user_id) REFERENCES platform_user (id) ON DELETE RESTRICT,
    CONSTRAINT ck_email_challenge_purpose
        CHECK (purpose IN ('bind', 'verify', 'change')),
    CONSTRAINT ck_email_challenge_attempt_count CHECK (attempt_count >= 0),
    CONSTRAINT ck_email_challenge_send_count CHECK (send_count >= 0),
    CONSTRAINT ck_email_challenge_active_hash
        CHECK (
            consumed_at IS NOT NULL
            OR superseded_at IS NOT NULL
            OR code_hash IS NOT NULL
        )
);

CREATE INDEX IF NOT EXISTS ix_email_verification_challenge_user
    ON email_verification_challenge (user_id);
CREATE INDEX IF NOT EXISTS ix_email_verification_challenge_email_created
    ON email_verification_challenge (email_canonical, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_email_verification_challenge_active
    ON email_verification_challenge (user_id)
    WHERE consumed_at IS NULL AND superseded_at IS NULL;

CREATE TABLE IF NOT EXISTS email_verification_event (
    id VARCHAR(40) NOT NULL,
    user_id VARCHAR(40) NOT NULL,
    email_canonical VARCHAR(254) NOT NULL,
    purpose VARCHAR(24) NOT NULL,
    challenge_id VARCHAR(40),
    event_type VARCHAR(24) NOT NULL,
    outcome VARCHAR(32) DEFAULT '' NOT NULL,
    ip VARCHAR(45) DEFAULT '' NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    CONSTRAINT pk_email_verification_event PRIMARY KEY (id),
    CONSTRAINT fk_email_verification_event_user
        FOREIGN KEY (user_id) REFERENCES platform_user (id) ON DELETE RESTRICT,
    CONSTRAINT fk_email_verification_event_challenge
        FOREIGN KEY (challenge_id)
        REFERENCES email_verification_challenge (id) ON DELETE SET NULL,
    CONSTRAINT ck_email_event_purpose
        CHECK (purpose IN ('bind', 'verify', 'change'))
);

CREATE INDEX IF NOT EXISTS ix_email_verification_event_email_time
    ON email_verification_event (email_canonical, created_at);
CREATE INDEX IF NOT EXISTS ix_email_verification_event_user_time
    ON email_verification_event (user_id, created_at);
CREATE INDEX IF NOT EXISTS ix_email_verification_event_ip_time
    ON email_verification_event (ip, created_at);
CREATE INDEX IF NOT EXISTS ix_email_verification_event_challenge_time
    ON email_verification_event (challenge_id, created_at);
