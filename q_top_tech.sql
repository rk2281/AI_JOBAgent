SELECT r.user_id, j.id AS job_id, left(j.title, 40) AS title,
       left(j.location, 25) AS location,
       round(r.semantic_raw::numeric, 3) AS sem_raw,
       round(r.final_score::numeric, 3) AS score,
       r.weight_covered AS cov,
       j.min_experience_years AS exp_min, j.max_experience_years AS exp_max,
       (SELECT count(*) FROM job_skills s WHERE s.job_id = j.id) AS n_skills,
       EXISTS (SELECT 1 FROM notifications n
               WHERE n.user_id = r.user_id AND n.job_id = j.id
                 AND n.status = 'SENT') AS already_sent
FROM recommendations r
JOIN jobs j ON j.id = r.job_id
WHERE j.is_active = true
  AND j.is_excluded = false
  AND j.source <> 'synthetic_test'
  AND j.skills_extracted_at IS NOT NULL
ORDER BY r.semantic_raw DESC
LIMIT 10