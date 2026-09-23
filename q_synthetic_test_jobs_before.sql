SELECT id, external_id, is_active
FROM jobs
WHERE source = 'synthetic_test'
ORDER BY id;
