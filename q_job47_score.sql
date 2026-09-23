SELECT round(final_score::numeric, 3) AS score, weight_covered,
       round(semantic_raw::numeric, 3) AS sem_raw,
       location_score, title_score, scoring_run_id
FROM recommendations
WHERE user_id = 13 AND job_id = 47