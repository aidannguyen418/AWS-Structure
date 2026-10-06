import json
import boto3

bedrock = boto3.client('bedrock-runtime')

def validate_message(event):
    try:
        body = json.loads(event['body'])
    except (json.JSONDecodeError, KeyError):
        return None, "Invalid JSON"

    goodkeys = {'sessionId', 'message'}
    if set(body.keys()) != goodkeys:
        return None, "Unexpected/Missing Fields"
    if not isinstance(body.get('sessionId'), str) or not body['sessionId'].strip():
        return None, "Invalid sessionID"
    if not isinstance(body.get('message'), str) or not body['message'].strip():
        return None, "Invalid message"
    return body, None

def message_limits(body):
    MaxMessageLength = 2000
    MaxSessionIdLength = 128

    if len(body['message']) > MaxMessageLength:
        return False, "Message longer than 2000 characters."

    if len(body['sessionId']) > MaxSessionIdLength:
        return False, "SessionId exceeds max length."

    return True, None

def structurebedrockrequest(body):
    return {
        "modelId": "amazon.nova-lite-v1:0",
        "messages": [
            {"role": "user", "content": [{"text": body["message"]}]}
        ]
    }

def callbedrock(bedrock_request):
    try:
        response = bedrock.converse(
            modelId = bedrock_request["modelId"],
            messages = bedrock_request["messages"]
        )
        return response, None
    except Exception as e:
        return None, str(e)

def errorresponse(status_code, message):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": message})
    }

def lambda_handler(event, context):
    body, error = validate_message(event)
    if error:
        return errorresponse(400, error)

    valid, error = message_limits(body)
    if not valid:
        return errorresponse(400, error)

    bedrock_request = structurebedrockrequest(body)
    response, error = callbedrock(bedrock_request)
    if error:
        return errorresponse(502, "AI service unavailable.")

    try: 
        reply_text = response['output']['message']['content'][0]['text']
    except(KeyError, IndexError):
        return errorresponse(502, "Unexpected AI Response.")

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"reply": reply_text})
    }