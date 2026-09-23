SELECT count(*) AS existed_before_job_695
FROM recommendations
WHERE user_id = 13
  AND created_at < (SELECT created_at FROM jobs WHERE id = 695);
