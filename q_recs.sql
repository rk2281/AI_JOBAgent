SELECT r.job_id, j.is_active, r.final_score, r.scoring_run_id
FROM recommendations r JOIN jobs j ON j.id = r.job_id
WHERE r.user_id = 13 AND r.job_id IN (593, 595, 694)
ORDER BY r.job_id