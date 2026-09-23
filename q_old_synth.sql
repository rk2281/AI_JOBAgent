SELECT id, source, external_id, title, company, location, work_mode,
       min_experience_years, max_experience_years, is_active,
       embedding_model
FROM jobs WHERE id IN (592, 593, 594, 595) ORDER BY id