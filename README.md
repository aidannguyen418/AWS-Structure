# Serverless AI Chatbot on AWS

A serverless chatbot on AWS: Cognito sign-in, API Gateway, Lambda, Bedrock (Nova Lite), and DynamoDB for conversation history. I built it twice, reverse-engineering a reference architecture in my own AWS account, to compare two ways of protecting the API with WAF.

| Version | Front door | Folder |
|---|---|---|
| HTTP API | CloudFront (with WAF) in front of an HTTP API with a JWT authorizer | [`http-api/`](http-api/) |
| REST API | A Regional WAF web ACL attached directly to a REST API stage, with a Cognito authorizer | [`rest-api/`](rest-api/) |

Each folder has its own README, handler, tests and Athena queries, so either version can be read on its own.

## HTTP API vs REST API

I moved to a REST API rather than HTTP API so I can take advantage of less attached parts and the built-in security features. Also to learn how a REST API is developed differently than an HTTP API.

| Service | HTTP API version | REST API version |
|---|---|---|
| WAF | Not attachable to an HTTP API directly, so CloudFront sits in front | Web ACL attaches to the stage directly |
| Extra pieces | CloudFront distribution | None in front of the API |
| Authorizer | Native JWT authorizer (works with Cognito tokens) | Cognito user pool authorizer. No native JWT authorizer, but a Lambda authorizer can validate JWTs |
| API features | Lacks API keys, per-client throttling, request validation | Has API keys, per-client throttling, request validation |
| Price per 1M requests | $1.00 | $3.50 |
| Deployment | Changes apply when saved | Changes apply after deploying to a stage |
| Raw API URL | Publicly reachable and bypasses CloudFront and WAF (tested) | TODO: record the result of the direct-URL test |



## Sources

- [Choose between HTTP APIs and REST APIs](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-vs-rest.html) (feature table: WAF, API keys, per-client throttling, request validation, authorizers)
- [API Gateway pricing](https://aws.amazon.com/api-gateway/pricing/)
