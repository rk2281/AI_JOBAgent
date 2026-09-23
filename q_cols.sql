SELECT column_name, is_nullable, (column_default IS NOT NULL) AS has_default
FROM information_schema.columns
WHERE table_name = 'jobs'
ORDER BY ordinal_position