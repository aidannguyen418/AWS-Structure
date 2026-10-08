-- Which rules are deciding outcomes?
SELECT action, terminatingruleid, count(*) AS requests
FROM chatbot_logs.waf_logs
WHERE log_time >= 'YYYY/MM/dd/HH/mm'
GROUP BY action, terminatingruleid
ORDER BY requests DESC;