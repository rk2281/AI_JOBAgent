SELECT id, status, trigger_source, sent_at
FROM notifications
WHERE user_id = 13 AND job_id = 47
ORDER BY id