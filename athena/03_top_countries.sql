-- Where is traffic coming from?
SELECT httprequest.country AS country, count(*) AS requests
FROM chatbot_logs.waf_logs
WHERE log_time >= 'YYYY/MM/dd/HH/mm'
GROUP BY httprequest.country
ORDER BY requests DESC
LIMIT 10;