UPDATE jobs
SET is_active = false
WHERE source = 'synthetic_test' AND is_active = true
RETURNING id, external_id, is_active;
