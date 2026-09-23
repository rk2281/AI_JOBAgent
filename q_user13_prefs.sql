SELECT user_id, target_roles, preferred_locations, min_experience_years,
       max_experience_years, remote_only, notification_threshold
FROM user_preferences
WHERE user_id = 13;
