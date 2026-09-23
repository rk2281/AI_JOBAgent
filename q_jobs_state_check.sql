SELECT count(*) AS total_jobs,
       count(*) FILTER (WHERE embedding IS NOT NULL) AS embedded,
       count(*) FILTER (WHERE is_excluded) AS excluded,
       count(*) FILTER (WHERE source = 'synthetic_test') AS synthetic_test,
       count(*) FILTER (WHERE source != 'synthetic_test') AS non_synthetic
FROM jobs;
