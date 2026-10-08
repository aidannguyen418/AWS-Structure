# WAF log analysis with Athena

SQL for querying AWS WAF logs from the chatbot's CloudFront distribution.

## How the pipeline works

CloudFront's web ACL → WAF logging → S3 bucket → Glue Data Catalog table → Athena

1. **WAF logging** writes gzip-compressed JSON logs to an S3 bucket every 5 minutes.
   AWS requires the bucket name to start with `aws-waf-logs-`.
2. The **table definition** (`01_create_table.sql`) is stored in the Glue Data Catalog.
   It describes the log schema; no data is copied.
3. **Athena** reads the compressed files in place and bills per data scanned.

Logs are stored under:
`AWSLogs/<ACCOUNT_ID>/WAFLogs/cloudfront/<web-acl-name>/YYYY/MM/dd/HH/mm/` (UTC).

## Design decisions

- **No Glue crawler.** AWS publishes the exact schema for WAF logs, so a crawler
  would add cost and re-scan the bucket for no benefit.
- **Partition projection** instead of registered partitions. Athena computes the
  `YYYY/MM/dd/HH/mm` folder paths from a formula, so there is nothing to maintain.
  The trade-off: Athena never checks that a folder exists, so a wrong `LOCATION`
  returns zero rows with no error.
- **Range starts at the first log day, not an earlier date.** Projection
  enumerates one folder per minute, so a wide range slows every query.
- **Always filter on `log_time`.** It decides which folders Athena reads and
  therefore how much data is scanned and billed.
- **Authorization header is redacted** in WAF logging, so JWTs are not stored in S3.

## Files

| File | Purpose |
|---|---|
| `01_create_table.sql` | Creates the `waf_logs` table (run once) |
| `02_rules_by_action.sql` | Requests grouped by action and terminating rule |
| `03_top_countries.sql` | Request counts by country |
| `04_requests_per_hour.sql` | Hourly traffic (UTC) |

## Using these files

1. Replace `<ACCOUNT_ID>`, `<LOG_BUCKET>` and `<WEB_ACL_NAME>` in `01_create_table.sql`.
2. Use the same region as the log bucket and set an Athena query results location.
3. Run `CREATE DATABASE chatbot_logs;`, then `01_create_table.sql`, then any query.
4. Make sure to remove all comments, as Athena does not handle SQL comments.

## What these logs do not show

- WAF only sees requests that pass through CloudFront. Calls to the raw API Gateway
  URL bypass it.
- WAF logs record WAF decisions. A request with no token shows as `ALLOW` here,
  because authentication is enforced later by API Gateway's JWT authorizer.

## Sources

- AWS WAF: logging to S3 (docs.aws.amazon.com/waf/latest/developerguide/logging-s3.html)
- Athena: create a table for WAF logs using partition projection
  (docs.aws.amazon.com/athena/latest/ug/create-waf-table-partition-projection.html)