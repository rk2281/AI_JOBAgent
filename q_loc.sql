SELECT r.job_id, left(j.location, 30) AS job_location,
       r.location_score, r.match_reasons
FROM recommendations r JOIN jobs j ON j.id = r.job_id
WHERE r.user_id = 13 AND r.job_id IN (47, 403, 49)
ORDER BY r.job_id