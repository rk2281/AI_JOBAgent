SELECT job_id, final_score, created_at, updated_at,
       (created_at = updated_at) AS looks_new
FROM recommendations
WHERE user_id = 13
ORDER BY updated_at DESC, job_id
LIMIT 15
