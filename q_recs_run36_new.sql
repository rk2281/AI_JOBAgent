SELECT count(*) AS new_rows
FROM recommendations
WHERE user_id = 13
  AND scoring_run_id = 36
  AND job_id = 695;
