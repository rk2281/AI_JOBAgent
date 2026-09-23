SELECT id, user_id, job_id, status, trigger_source
FROM notifications
WHERE id > 24
ORDER BY id;
