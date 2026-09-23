SELECT r.job_id, r.scoring_run_id, r.created_at, j.is_active, j.is_excluded, j.exclusion_reason
FROM recommendations r
JOIN jobs j ON j.id = r.job_id
WHERE r.user_id = 13
  AND r.created_at < (SELECT created_at FROM jobs WHERE id = 695)
  AND (r.scoring_run_id IS DISTINCT FROM 36)
ORDER BY r.job_id;
