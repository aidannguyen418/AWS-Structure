-- Traffic per hour (UTC)
SELECT date_trunc('hour', from_unixtime("timestamp" / 1000)) AS hour_utc,
       count(*) AS requests
FROM chatbot_logs.waf_logs
WHERE log_time >= 'YYYY/MM/dd/HH/mm'
GROUP BY 1
ORDER BY 1;