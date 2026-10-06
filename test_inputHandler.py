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