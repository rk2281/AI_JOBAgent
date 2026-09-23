SELECT r.job_id, left(j.location, 40) AS job_location,
       r.location_score
FROM recommendations r JOIN jobs j ON j.id = r.job_id
WHERE r.user_id = 13
  AND j.is_active = true
  AND j.location ILIKE '%delhi%'
ORDER BY r.location_score NULLS FIRST, r.job_id
LIMIT 15
