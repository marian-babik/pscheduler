--
-- Run Table
--

DROP TABLE IF EXISTS run CASCADE;
CREATE TABLE run (

	-- Row identifier
	id		BIGSERIAL
			PRIMARY KEY,

	-- External-use identifier
	uuid		UUID
			UNIQUE
			DEFAULT gen_random_uuid(),

	-- Task this run belongs to
	task		BIGINT
			REFERENCES task(id)
			ON DELETE CASCADE,

	-- Range of times when this task will be run
	times		TSTZRANGE
			NOT NULL,

	--
	-- Information about the local system's participation in the
	-- test
	--

        -- Participant data for the local test
        part_data        JSONB,

	-- State of this run
	state	    	 INTEGER DEFAULT run_state_pending()
			 REFERENCES run_state(id),

	-- How it went locally, i.e., what the test returned
	status           INTEGER,

	-- Result from the local run
	result   	 JSONB,

	-- Result from the local run
	errors   	 TEXT,


	--
	-- Information about the whole test
	--

        -- Participant data for all participants in the test.  This is
        -- an array, with each element being the part_data for
        -- each participant.
        part_data_full   JSONB,

	-- Combined resut generated by the lead participant
	result_full    	 JSONB,

	-- Merged result generated by the tool that did the test
	result_merged  	 JSONB
);


-- This should be used when someone looks up the external ID.  Bring
-- the row ID a long so it can be pulled without having to consult the
-- table.
DROP INDEX IF EXISTS run_uuid;
CREATE INDEX run_uuid
ON task(uuid, id);



DROP INDEX IF EXISTS run_times;
-- GIST accelerates range-specific operators like &&
CREATE INDEX run_times ON run USING GIST (times);

-- These two indexes are used by the schedule_gap view.

DROP INDEX IF EXISTS run_times_lower;
CREATE INDEX run_times_lower ON run(lower(times), state);

DROP INDEX IF EXISTS run_times_upper;
CREATE INDEX run_times_upper ON run(upper(times));



CREATE OR REPLACE FUNCTION run_alter()
RETURNS TRIGGER
AS $$
DECLARE
    horizon INTERVAL;
    task RECORD;
    tool_name TEXT;
    run_result external_program_result;
    pdata_out JSONB;
    result_merge_input JSONB;
BEGIN

    -- TODO: What changes to a run don't we allow?

    -- No scheduling beyond the time horizon
    SELECT INTO horizon duration FROM schedule_time_horizon;
    IF (upper(NEW.times) - now()) > horizon THEN
        RAISE EXCEPTION 'Cannot schedule runs more than % in advance', horizon;
    END IF;

    -- Disallow overlap
    -- TODO: Remove this when the scheduler can decide which run runs

    IF ( (TG_OP = 'INSERT')
         AND (EXISTS (SELECT * FROM run WHERE run.times && NEW.times)) )
       OR ( (TG_OP = 'UPDATE')  -- TODO:  Do we want to allow times to change?
            AND (NEW.times != OLD.times)
            AND (EXISTS (SELECT * FROM run WHERE id != NEW.id AND run.times && NEW.times)) )
    THEN
       RAISE EXCEPTION 'Runs may not overlap with others.';
    END IF;


    -- Make sure UUID assignment follows a sane pattern.

    SELECT INTO task * FROM task WHERE id = NEW.task;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'No task % exists.', NEW.task;
    END IF;

    IF (TG_OP = 'INSERT') THEN

        IF task.participant = 0 THEN
	    -- Lead participant should be assigning a UUID
            IF NEW.uuid IS NOT NULL THEN
                RAISE EXCEPTION 'Lead participant should not be given a run UUID.';
            END IF;
            NEW.uuid := gen_random_uuid();
        ELSE
            -- Non-leads should be given a UUID.
            IF NEW.uuid IS NULL THEN
                RAISE EXCEPTION 'Non-lead participant should not be assigning a run UUID.';
            END IF;
        END IF;

    ELSIF (TG_OP = 'UPDATE') THEN

        IF NEW.uuid <> OLD.uuid THEN
            RAISE EXCEPTION 'UUID cannot be changed';
        END IF;

	-- TODO: Make sure part_data_full, result_ful and
	-- result_merged happen in the right order.

    END IF;


    -- TODO: When there's resource management, assign the resources to this run.


    -- TODO: New runs get participant data.  The table needs a column for this.

    pdata_out := row_to_json(t) FROM ( SELECT task.participant AS participant,
                                       task.json #>> '{test, spec}' AS test ) t;

    SELECT INTO tool_name name FROM tool WHERE id = task.tool;

    run_result := pscheduler_internal(ARRAY['invoke', 'tool', tool_name, 'participant-data'], pdata_out::TEXT );
	IF run_result.status != 0 THEN
	    RAISE EXCEPTION 'Unable to get participant data: %', run_result.stderr;
	END IF;
    NEW.part_data := regexp_replace(run_result.stdout, '\s+$', '')::JSONB;


    IF (TG_OP = 'UPDATE') THEN

        -- Change the state automatically if the status from the run
	-- changes.

        IF (NEW.status IS NOT NULL) THEN

            IF lower(NEW.times) > normalized_now() THEN
  	        RAISE EXCEPTION 'Cannot set state on future runs. % / %', lower(NEW.times), normalized_now();
	    END IF;

            NEW.state := CASE NEW.status
    	        WHEN 0 THEN run_state_finished()
	        ELSE        run_state_failed()
	        END;
        END IF;

	IF NOT run_state_transition_is_valid(OLD.state, NEW.state) THEN
            RAISE EXCEPTION 'Invalid transition between states.';
        END IF;

	-- If the full result changed, update the merged version.

       IF NEW.result_full IS NOT NULL
          AND COALESCE(NEW.result_full::TEXT, '')
	      <> COALESCE(OLD.result_full::TEXT, '') THEN

	       result_merge_input := row_to_json(t) FROM (
	           SELECT 
		       task.json #>> '{test}' AS test,
                       NEW.result_full AS results
                   ) t;

 	       run_result := pscheduler_internal(ARRAY['invoke', 'tool', tool_name,
	           'merged-results'], result_merge_input::TEXT );
   	       IF run_result.status != 0 THEN
	           RAISE EXCEPTION 'Unable to get merged result: %', run_result.stderr;
	       END IF;
  	      NEW.result_merged := regexp_replace(run_result.stdout, '\s+$', '')::JSONB;

	      NOTIFY result_available;

        ELSIF NEW.result_full IS NULL THEN

            NEW.result_merged := NULL;

        END IF;

    END IF;

    NOTIFY run_change;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER run_alter BEFORE INSERT OR UPDATE ON run
       FOR EACH ROW EXECUTE PROCEDURE run_alter();





-- TODO: Should do a trigger after any change to run that calls
-- run_main_minute() to update any run states.




-- Maintenance functions

CREATE OR REPLACE FUNCTION run_handle_stragglers()
RETURNS VOID
AS $$
BEGIN

    -- Runs that are still pending after their start times were
    -- missed.

    UPDATE run
    SET state = run_state_missed()
    WHERE
        state = run_state_pending()
	-- TODO: This interval should probably be a tunable.
	AND lower(times) < normalized_now() - 'PT5S'::interval;

    -- Runs that started and didn't report back in a timely manner

    UPDATE run
    SET
        state = run_state_overdue()
    WHERE
        state = run_state_running()
	-- TODO: This interval should probably be a tunable.
	AND upper(times) < normalized_now() - 'PT10S'::interval;

    -- Runs still running well after their expected completion times
    -- are treated as having failed.

    UPDATE run
    SET
        state = run_state_missed()
    WHERE
        state = run_state_overdue()
	-- TODO: This interval should probably be a tunable.
	AND upper(times) < normalized_now() - 'PT1M'::interval;

END;
$$ LANGUAGE plpgsql;



-- Maintenance that happens once per minute.

CREATE OR REPLACE FUNCTION run_maint_fifteen()
RETURNS VOID
AS $$
BEGIN
    PERFORM run_handle_stragglers();
END;
$$ LANGUAGE plpgsql;



-- Convenient ways to see the goings on

CREATE OR REPLACE VIEW run_status
AS
    SELECT
        run.id AS run,
	run.uuid AS run_uuid,
	task.id AS task,
	task.uuid AS task_uuid,
	test.name AS test,
	tool.name AS tool,
	run.times,
	run_state.display AS state
    FROM
        run
	JOIN run_state ON run_state.id = run.state
	JOIN task ON task.id = task
	JOIN test ON test.id = task.test
	JOIN tool ON tool.id = task.tool
    WHERE
        run.state != run_state_pending()
	OR (run.state = run_state_pending()
            AND lower(run.times) < (now() + 'PT2M'::interval))
    ORDER BY run.times;


CREATE VIEW run_status_short
AS
    SELECT run, task, times, state
    FROM  run_status
;



--
-- API
--

-- Put a run of a task on the schedule.

CREATE OR REPLACE FUNCTION api_run_post(
    task_uuid UUID,
    start_time TIMESTAMP WITH TIME ZONE,
    run_uuid UUID = NULL
)
RETURNS UUID
AS $$
DECLARE
    task RECORD;
    time_range TSTZRANGE;
BEGIN

    SELECT INTO task * FROM task WHERE uuid = task_uuid;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'No task % exists.', task_uuid;
    END IF;

    IF run_uuid IS NULL AND task.participant <> 0 THEN
        RAISE EXCEPTION 'Cannot set run UUID as non-lead participant';
    END IF;

    start_time := normalized_time(start_time);
    time_range := tstzrange(start_time, start_time + task.duration, '[)');

    WITH inserted_row AS (
        INSERT INTO run (uuid, task, times)
        VALUES (run_uuid, task.id, time_range)
        RETURNING *
    ) SELECT INTO run_uuid uuid FROM inserted_row;

    RETURN run_uuid;

END;
$$ LANGUAGE plpgsql;


-- TODO: Need api_run_get()
-- TODO: Need api_run_patch()
-- TODO: Need api_run_delete()




