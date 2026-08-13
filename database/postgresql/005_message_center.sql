-- N06: auditable announcement withdrawal reason.

ALTER TABLE IF EXISTS announcement
    ADD COLUMN IF NOT EXISTS withdrawal_reason VARCHAR(500);

-- N07: recipient authorization uses only platform_user.id. Convert unambiguous
-- legacy username rows before the API stops consulting the username namespace.
-- If a username collides with another user's stable ID, keep the stable-ID
-- interpretation; this prevents an account-name collision from granting access.
DELETE FROM message_recipient AS legacy
USING platform_user AS owner, message_recipient AS stable
WHERE legacy.user_id = owner.username
  AND legacy.user_id <> owner.id
  AND NOT EXISTS (
      SELECT 1 FROM platform_user AS id_owner WHERE id_owner.id = legacy.user_id
  )
  AND stable.message_id = legacy.message_id
  AND stable.user_id = owner.id
  AND stable.id <> legacy.id;

UPDATE message_recipient AS recipient
SET user_id = owner.id
FROM platform_user AS owner
WHERE recipient.user_id = owner.username
  AND recipient.user_id <> owner.id
  AND NOT EXISTS (
      SELECT 1 FROM platform_user AS id_owner WHERE id_owner.id = recipient.user_id
  );

DELETE FROM user_membership AS legacy
USING platform_user AS owner, user_membership AS stable
WHERE legacy.user_id = owner.username
  AND legacy.user_id <> owner.id
  AND NOT EXISTS (
      SELECT 1 FROM platform_user AS id_owner WHERE id_owner.id = legacy.user_id
  )
  AND stable.user_id = owner.id
  AND stable.organization_id = legacy.organization_id
  AND stable.position_code = legacy.position_code
  AND stable.id <> legacy.id;

UPDATE user_membership AS membership
SET user_id = owner.id
FROM platform_user AS owner
WHERE membership.user_id = owner.username
  AND membership.user_id <> owner.id
  AND NOT EXISTS (
      SELECT 1 FROM platform_user AS id_owner WHERE id_owner.id = membership.user_id
  );

UPDATE data_scope_policy AS policy
SET subject_id = owner.id
FROM platform_user AS owner
WHERE policy.subject_type = 'user'
  AND policy.subject_id = owner.username
  AND policy.subject_id <> owner.id
  AND NOT EXISTS (
      SELECT 1 FROM platform_user AS id_owner WHERE id_owner.id = policy.subject_id
  )
  AND NOT EXISTS (
      SELECT 1
      FROM data_scope_policy AS canonical
      WHERE canonical.subject_type = 'user'
        AND canonical.subject_id = owner.id
        AND canonical.business_key = policy.business_key
  );

CREATE OR REPLACE FUNCTION protect_frozen_announcement()
RETURNS trigger
LANGUAGE plpgsql
AS $freeze$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status <> 'draft' THEN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'published announcement history cannot be deleted';
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.status IN ('withdrawn', 'expired') THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'terminal announcement history is immutable';
    END IF;

    IF OLD.status = 'draft' AND NEW.status NOT IN ('draft', 'published') THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'draft announcement can only remain draft or be published';
    END IF;

    IF OLD.status = 'draft' AND NEW.status = 'published' AND (
        NEW.published_by IS NULL OR NEW.published_at IS NULL
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'published announcement requires publisher and publish time';
    END IF;

    IF OLD.status = 'published' AND (
        NEW.status NOT IN ('published', 'withdrawn', 'expired')
        OR NEW.supersedes_announcement_id IS DISTINCT FROM OLD.supersedes_announcement_id
        OR NEW.revision IS DISTINCT FROM OLD.revision
        OR NEW.title IS DISTINCT FROM OLD.title
        OR NEW.body IS DISTINCT FROM OLD.body
        OR NEW.priority IS DISTINCT FROM OLD.priority
        OR NEW.is_pinned IS DISTINCT FROM OLD.is_pinned
        OR NEW.audience_json IS DISTINCT FROM OLD.audience_json
        OR NEW.audience_scope_hash IS DISTINCT FROM OLD.audience_scope_hash
        OR NEW.created_by IS DISTINCT FROM OLD.created_by
        OR NEW.created_at IS DISTINCT FROM OLD.created_at
        OR NEW.published_by IS DISTINCT FROM OLD.published_by
        OR NEW.valid_from IS DISTINCT FROM OLD.valid_from
        OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
        OR NEW.published_at IS DISTINCT FROM OLD.published_at
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'published announcement content is immutable';
    END IF;

    IF OLD.status = 'published' AND NEW.status = 'published'
        AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'published announcement lifecycle can only transition';
    END IF;

    IF OLD.status = 'published' AND NEW.status = 'withdrawn' AND (
        NEW.withdrawn_by IS NULL
        OR NEW.withdrawn_at IS NULL
        OR COALESCE(BTRIM(NEW.withdrawal_reason), '') = ''
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'withdrawn announcement requires actor, time and reason';
    END IF;

    IF OLD.status = 'published' AND NEW.status = 'expired' AND (
        NEW.withdrawn_by IS DISTINCT FROM OLD.withdrawn_by
        OR NEW.withdrawn_at IS DISTINCT FROM OLD.withdrawn_at
        OR NEW.withdrawal_reason IS DISTINCT FROM OLD.withdrawal_reason
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'expiration cannot modify withdrawal metadata';
    END IF;
    RETURN NEW;
END
$freeze$;

DROP TRIGGER IF EXISTS trg_announcement_frozen ON announcement;
CREATE TRIGGER trg_announcement_frozen
BEFORE UPDATE OR DELETE ON announcement
FOR EACH ROW
EXECUTE FUNCTION protect_frozen_announcement();
