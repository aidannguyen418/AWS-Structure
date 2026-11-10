import json
import os
import time

import boto3
from boto3.dynamodb.conditions import Key

bedrock = boto3.client('bedrock-runtime')
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(os.environ.get('HISTORY_TABLE', 'ChatbotConvoHistory'))

MAX_HISTORY_TURNS = 5          # how many past exchanges we are willing to read
MAX_HISTORY_CHARS = 6000       # total characters of history we are willing to send
HISTORY_TTL_SECONDS = 7 * 24 * 60 * 60

def validate_message(event):
    try:
        body = json.loads(event['body'])
    except (json.JSONDecodeError, KeyError, TypeError):
        return None, "Invalid JSON"
    
    if not isinstance(body, dict):
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

def get_user_id(event):
    """The Cognito 'sub' that API Gateway verified. Never taken from the request body."""
    try:
        sub = event['requestContext']['authorizer']['jwt']['claims']['sub']
    except (KeyError, TypeError):
        return None
    if not isinstance(sub, str) or not sub.strip():
        return None
    return sub


def load_history(user_id, session_id):
    """Return the most recent exchanges as alternating user/assistant messages, oldest first."""
    try:
        result = table.query(
            KeyConditionExpression=Key('pk').eq(f"{user_id}#{session_id}"),
            ScanIndexForward=False,        # newest first
            Limit=MAX_HISTORY_TURNS,       # DynamoDB stops reading after this many items
            ConsistentRead=True,           # make sure the previous exchange is visible
        )
    except Exception as e:
        print(f"History load failed: {e}")   # goes to CloudWatch Logs
        return []

    kept = []
    used = 0
    for item in result.get('Items', []):     # newest first
        size = len(item['userText']) + len(item['assistantText'])
        if used + size > MAX_HISTORY_CHARS:
            break                            # stop, don't skip: no gaps in the window
        kept.append(item)
        used += size
    kept.reverse()                           # now oldest first

    messages = []
    for item in kept:
        messages.append({"role": "user", "content": [{"text": item['userText']}]})
        messages.append({"role": "assistant", "content": [{"text": item['assistantText']}]})
    return messages


def save_exchange(user_id, session_id, user_text, assistant_text):
    now = time.time()
    try:
        table.put_item(Item={
            'pk': f"{user_id}#{session_id}",
            'ts': int(now * 1000),                          # sort key, milliseconds
            'userText': user_text,
            'assistantText': assistant_text,
            'expiresAt': int(now) + HISTORY_TTL_SECONDS,    # TTL attribute, SECONDS
        })
    except Exception as e:
        print(f"History save failed: {e}")

def structurebedrockrequest(body, history=None):
    messages = list(history or [])
    messages.append({"role": "user", "content": [{"text": body["message"]}]})
    return {
        "modelId": "amazon.nova-lite-v1:0",
        "messages": messages
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

    user_id = get_user_id(event)
    if user_id is None:
        return errorresponse(401, "Unauthorized.")

    history = load_history(user_id, body['sessionId'])
    bedrock_request = structurebedrockrequest(body, history)
    response, error = callbedrock(bedrock_request)
    if error:
        return errorresponse(502, "AI service unavailable.")

    try:
        reply_text = response['output']['message']['content'][0]['text']
    except (KeyError, IndexError):
        return errorresponse(502, "Unexpected AI Response.")

    if reply_text.strip():
        save_exchange(user_id, body['sessionId'], body['message'], reply_text)

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"reply": reply_text})
    }