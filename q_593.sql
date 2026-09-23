SELECT j.id, j.is_excluded,
       (j.embedding IS NOT NULL) AS embedded,
       (j.skills_extracted_at IS NOT NULL) AS enriched,
       j.skills_extraction_model,
       (SELECT count(*) FROM job_skills s WHERE s.job_id = j.id) AS n_skills,
       (SELECT r.final_score FROM recommendations r
         WHERE r.user_id = 13 AND r.job_id = j.id) AS old_score,
       (SELECT r.weight_covered FROM recommendations r
         WHERE r.user_id = 13 AND r.job_id = j.id) AS old_coverage
FROM jobs j WHERE j.id = 593