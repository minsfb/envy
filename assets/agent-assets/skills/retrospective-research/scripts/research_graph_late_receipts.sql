-- Storage revision 3; packet protocol 1 and previous migration bytes are frozen.
-- A late terminal receipt supersedes the observed outcome of a request-only
-- cancellation. The controller additionally validates exact recorder evidence
-- and accepted worker-stop history. Cancellation/dispatch events remain intact.
DROP TRIGGER operation_transition;
CREATE TRIGGER operation_transition BEFORE UPDATE OF execution_state ON operation_links WHEN NEW.execution_state <> OLD.execution_state BEGIN
    SELECT RAISE(ABORT, 'invalid operation transition') WHERE NOT (
        (OLD.execution_state = 'reserved' AND NEW.execution_state IN ('admitted', 'cancelled', 'unknown')) OR
        (OLD.execution_state = 'admitted' AND NEW.execution_state IN ('dispatched', 'cancelled', 'unknown')) OR
        (OLD.execution_state = 'dispatched' AND NEW.execution_state IN ('succeeded', 'failed', 'interrupted', 'unknown')) OR
        (OLD.execution_state IN ('interrupted', 'unknown') AND NEW.execution_state IN ('succeeded', 'failed', 'cancelled', 'unknown')) OR
        (OLD.execution_state = 'cancelled' AND OLD.receipt_sha256 IS NULL
         AND NEW.execution_state IN ('succeeded', 'failed') AND NEW.receipt_sha256 IS NOT NULL
         AND NEW.assignment_id IS NOT NULL AND NEW.budget_event_id IS NOT NULL
         AND NEW.verification_state = 'verified'));
END;
PRAGMA user_version = 3;
