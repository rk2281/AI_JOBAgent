SELECT id, source, external_id, title, location, is_active, is_excluded,
       (embedding IS NOT NULL) AS embedded,
       (skills_extracted_at IS NOT NULL) AS enriched,
       embedding_model,
       (SELECT count(*) FROM job_skills s WHERE s.job_id = 694) AS n_skills
FROM jobs WHERE id = 694