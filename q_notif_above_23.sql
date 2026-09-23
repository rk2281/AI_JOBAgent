SELECT id, user_id, job_id, status, trigger_source
FROM notifications
WHERE id > 23
ORDER BY id;
