SELECT current_title, location, total_experience_years,
       jsonb_array_length(skills) AS n_skills, skills
FROM profiles WHERE user_id = 13