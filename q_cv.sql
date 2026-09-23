SELECT p.user_id, p.active_cv_version_id,
       (v.embedding IS NOT NULL) AS embedded,
       v.embedded_at, v.embedding_model
FROM profiles p
LEFT JOIN cv_versions v ON v.id = p.active_cv_version_id
WHERE p.user_id = 13