WITH new_job AS (
  INSERT INTO jobs (
    source, external_id, title, company, location, description, url,
    is_remote, min_experience_years, max_experience_years, posted_at,
    content_hash, is_active, embedding, last_seen_at,
    embedding_model, embedded_at, embedding_attempts, embedding_error,
    embedding_source_hash, skills_extracted_at, skills_extraction_model,
    skills_extraction_attempts, skills_extraction_error, skills_source_hash,
    work_mode, is_excluded, exclusion_reason
  )
  SELECT
    source, 'user13-synthetic-day16-' || to_char(now(), 'YYYYMMDDHH24MI'),
    title, company, location,
    description || ' [DAY 16 STAGE 3.3 TEST JOB 2026-09-23: synthetic, cloned from job 593 for live verification. Not a real posting.]',
    url, is_remote, min_experience_years, max_experience_years, now(),
    encode(sha256(convert_to('user13-synthetic-day16-' || clock_timestamp()::text, 'UTF8')), 'hex'),
    true, embedding, now(),
    embedding_model, now(), embedding_attempts, embedding_error,
    embedding_source_hash, now(), skills_extraction_model,
    skills_extraction_attempts, skills_extraction_error, skills_source_hash,
    work_mode, false, NULL
  FROM jobs WHERE id = 593
  RETURNING id
),
new_skills AS (
  INSERT INTO job_skills (job_id, skill_id, is_required)
  SELECT n.id, s.skill_id, s.is_required
  FROM new_job n CROSS JOIN job_skills s
  WHERE s.job_id = 593
  RETURNING job_id
)
SELECT (SELECT id FROM new_job) AS new_job_id,
       (SELECT count(*) FROM new_skills) AS skills_copied
