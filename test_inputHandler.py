import json
import os

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-2")  # must come BEFORE the import below

import inputHandler  # change to your file's name, without .py


def make_event(payload):
    """Build an event shaped like API Gateway's: the body is a JSON *string*."""
    return {"body": json.dumps(payload)}


def test_valid_request_passes():
    body, error = inputHandler.validate_message(make_event({"sessionId": "abc-123", "message": "Hello"}))
    assert error is None
    assert body == {"sessionId": "abc-123", "message": "Hello"}


def test_invalid_json_is_rejected():
    body, error = inputHandler.validate_message({"body": "this is not json"})
    assert body is None
    assert error == "Invalid JSON"


def test_missing_body_key_is_rejected():
    body, error = inputHandler.validate_message({})
    assert body is None
    assert error == "Invalid JSON"


def test_extra_field_is_rejected():
    body, error = inputHandler.validate_message(make_event({"sessionId": "a", "message": "hi", "admin": True}))
    assert body is None
    assert error == "Unexpected/Missing Fields"


def test_whitespace_only_message_is_rejected():
    body, error = inputHandler.validate_message(make_event({"sessionId": "a", "message": "   "}))
    assert body is None
    assert error == "Invalid message"


def test_non_string_session_id_is_rejected():
    body, error = inputHandler.validate_message(make_event({"sessionId": 12345, "message": "hi"}))
    assert body is None
    assert error == "Invalid sessionID"


def test_null_body_is_rejected_cleanly():
    body, error = inputHandler.validate_message({"body": None})
    assert body is None
    assert error is not None


def test_json_that_is_not_an_object_is_rejected():
    body, error = inputHandler.validate_message({"body": "[1, 2, 3]"})
    assert body is None
    assert error is not None

    # --- message_limits ---

def test_message_exactly_at_limit_passes():
    ok, error = inputHandler.message_limits({"sessionId": "a", "message": "x" * 2000})
    assert ok is True
    assert error is None


def test_message_over_limit_is_rejected():
    ok, error = inputHandler.message_limits({"sessionId": "a", "message": "x" * 2001})
    assert ok is False
    assert error == "Message longer than 2000 characters."


def test_session_id_over_limit_is_rejected():
    ok, error = inputHandler.message_limits({"sessionId": "s" * 129, "message": "hi"})
    assert ok is False
    assert error == "SessionId exceeds max length."


def test_session_id_exactly_at_limit_passes():
    ok, error = inputHandler.message_limits({"sessionId": "s" * 128, "message": "hi"})
    assert ok is True
    assert error is None

# --- structurebedrockrequest ---

def test_bedrock_request_has_expected_shape():
    request = inputHandler.structurebedrockrequest({"sessionId": "a", "message": "Hello"})
    assert request == {
        "modelId": "amazon.nova-lite-v1:0",
        "messages": [{"role": "user", "content": [{"text": "Hello"}]}],
    }

# --- errorresponse ---

def test_error_response_matches_api_gateway_shape():
    response = inputHandler.errorresponse(400, "Bad thing")
    assert response["statusCode"] == 400
    assert response["headers"] == {"Content-Type": "application/json"}
    assert isinstance(response["body"], str)  # API Gateway needs a string, not a dict
    assert json.loads(response["body"]) == {"error": "Bad thing"}

class FakeBedrock:
    """Stands in for the real boto3 client. Records every call, makes no network request."""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []  # one dict of keyword arguments per converse() call

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def good_reply(text="Hi there!"):
    """The nested shape Bedrock's converse() returns."""
    return {"output": {"message": {"role": "assistant", "content": [{"text": text}]}}}


VALID_EVENT = make_event({"sessionId": "abc-123", "message": "Hello"})


def test_success_path_returns_200_and_reply(monkeypatch):
    fake = FakeBedrock(response=good_reply("Hi there!"))
    monkeypatch.setattr(inputHandler, "bedrock", fake)

    result = inputHandler.lambda_handler(VALID_EVENT, None)

    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == {"reply": "Hi there!"}


def test_converse_is_called_with_the_right_argument_names(monkeypatch):
    # This is the test that would have caught the `messaage` typo.
    fake = FakeBedrock(response=good_reply())
    monkeypatch.setattr(inputHandler, "bedrock", fake)

    inputHandler.lambda_handler(VALID_EVENT, None)

    assert len(fake.calls) == 1
    assert set(fake.calls[0].keys()) == {"modelId", "messages"}
    assert fake.calls[0]["modelId"] == "amazon.nova-lite-v1:0"
    assert fake.calls[0]["messages"] == [
        {"role": "user", "content": [{"text": "Hello"}]}
    ]


def test_bedrock_failure_returns_502_without_leaking_details(monkeypatch):
    secret = "AccessDeniedException: arn:aws:iam::123456789012:role/internal-role"
    fake = FakeBedrock(error=RuntimeError(secret))
    monkeypatch.setattr(inputHandler, "bedrock", fake)

    result = inputHandler.lambda_handler(VALID_EVENT, None)

    assert result["statusCode"] == 502
    assert "AI service unavailable" in result["body"]
    assert "AccessDenied" not in result["body"]
    assert "arn:aws" not in result["body"]


def test_malformed_bedrock_response_returns_502(monkeypatch):
    fake = FakeBedrock(response={"output": {"message": {"content": []}}})  # empty content list
    monkeypatch.setattr(inputHandler, "bedrock", fake)

    result = inputHandler.lambda_handler(VALID_EVENT, None)

    assert result["statusCode"] == 502
    assert "Unexpected AI Response" in result["body"]


def test_invalid_input_returns_400_and_never_calls_bedrock(monkeypatch):
    fake = FakeBedrock(response=good_reply())
    monkeypatch.setattr(inputHandler, "bedrock", fake)

    result = inputHandler.lambda_handler({"body": "not json"}, None)

    assert result["statusCode"] == 400
    assert fake.calls == []  # fail fast, fail cheap: no Bedrock spend


def test_over_limit_message_returns_400_and_never_calls_bedrock(monkeypatch):
    fake = FakeBedrock(response=good_reply())
    monkeypatch.setattr(inputHandler, "bedrock", fake)
    event = make_event({"sessionId": "abc-123", "message": "x" * 2001})

    result = inputHandler.lambda_handler(event, None)

    assert result["statusCode"] == 400
    assert fake.calls == []