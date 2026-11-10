import json
import os
import time
import pytest

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-2")  # must come BEFORE the import below

import inputHandler  # change to your file's name, without .py


def make_event(payload, sub="user-1"):
    """Build an event shaped like API Gateway's: body is a JSON *string*, plus the verified user."""
    event = {"body": json.dumps(payload)}
    if sub is not None:
        event["requestContext"] = {"authorizer": {"jwt": {"claims": {"sub": sub}}}}
    return event


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

class FakeTable:
    """Stands in for the DynamoDB Table. Remembers items and honours pk / newest-first / Limit."""

    def __init__(self, fail_query=False, fail_put=False):
        self.items = []
        self.query_calls = []
        self.put_calls = []
        self.fail_query = fail_query
        self.fail_put = fail_put

    def add(self, pk, ts, user_text, assistant_text):
        self.items.append({"pk": pk, "ts": ts, "userText": user_text, "assistantText": assistant_text})

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        if self.fail_query:
            raise RuntimeError("DynamoDB is down")
        wanted_pk = kwargs["KeyConditionExpression"].get_expression()["values"][1]
        rows = sorted((i for i in self.items if i["pk"] == wanted_pk), key=lambda i: i["ts"])
        if kwargs.get("ScanIndexForward", True) is False:
            rows.reverse()
        return {"Items": rows[: kwargs.get("Limit", len(rows))]}

    def put_item(self, **kwargs):
        if self.fail_put:
            raise RuntimeError("DynamoDB is down")
        self.put_calls.append(kwargs["Item"])


@pytest.fixture(autouse=True)
def fake_table(monkeypatch):
    table = FakeTable()
    monkeypatch.setattr(inputHandler, "table", table)
    return table

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

# ---------------- conversation history ----------------

def test_history_is_sent_oldest_first_and_alternates(monkeypatch, fake_table):
    fake = FakeBedrock(response=good_reply())
    monkeypatch.setattr(inputHandler, "bedrock", fake)
    fake_table.add("user-1#abc-123", 2000, "second question", "second answer")
    fake_table.add("user-1#abc-123", 1000, "first question", "first answer")  # out of order on purpose

    inputHandler.lambda_handler(VALID_EVENT, None)

    messages = fake.calls[0]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant", "user"]
    assert [m["content"][0]["text"] for m in messages] == [
        "first question", "first answer", "second question", "second answer", "Hello",
    ]


def test_query_reads_only_the_newest_exchanges(fake_table, monkeypatch):
    monkeypatch.setattr(inputHandler, "bedrock", FakeBedrock(response=good_reply()))

    inputHandler.lambda_handler(VALID_EVENT, None)

    call = fake_table.query_calls[0]
    assert call["Limit"] == inputHandler.MAX_HISTORY_TURNS
    assert call["ScanIndexForward"] is False
    assert call["ConsistentRead"] is True
    assert call["KeyConditionExpression"].get_expression()["values"][1] == "user-1#abc-123"


def test_only_the_newest_five_of_many_exchanges_are_sent(monkeypatch, fake_table):
    fake = FakeBedrock(response=good_reply())
    monkeypatch.setattr(inputHandler, "bedrock", fake)
    for n in range(1, 9):  # eight stored exchanges, oldest is n=1
        fake_table.add("user-1#abc-123", n * 1000, f"q{n}", f"a{n}")

    inputHandler.lambda_handler(VALID_EVENT, None)

    texts = [m["content"][0]["text"] for m in fake.calls[0]["messages"]]
    assert texts[0] == "q4"            # q4..q8 are the newest five
    assert "q3" not in texts
    assert len(texts) == 5 * 2 + 1


def test_character_budget_drops_the_oldest_exchanges(monkeypatch, fake_table):
    fake = FakeBedrock(response=good_reply())
    monkeypatch.setattr(inputHandler, "bedrock", fake)
    # 2,500 chars each: two fit in the 6,000 budget, a third does not
    fake_table.add("user-1#abc-123", 1000, "OLD" + "x" * 1247, "y" * 1250)
    fake_table.add("user-1#abc-123", 2000, "MID" + "x" * 1247, "y" * 1250)
    fake_table.add("user-1#abc-123", 3000, "NEW" + "x" * 1247, "y" * 1250)
    fake_table.add("user-1#abc-123", 4000, "NEWEST" + "x" * 1244, "y" * 1250)

    inputHandler.lambda_handler(VALID_EVENT, None)

    user_texts = [m["content"][0]["text"] for m in fake.calls[0]["messages"] if m["role"] == "user"]
    assert [t[:3] for t in user_texts[:-1]] == ["NEW", "NEW"]  # NEW and NEWEST kept
    assert not any(t.startswith("OLD") or t.startswith("MID") for t in user_texts)


def test_window_stops_at_a_too_big_exchange_instead_of_skipping_it(monkeypatch, fake_table):
    fake = FakeBedrock(response=good_reply())
    monkeypatch.setattr(inputHandler, "bedrock", fake)
    fake_table.add("user-1#abc-123", 1000, "tiny old question", "tiny old answer")
    fake_table.add("user-1#abc-123", 2000, "x" * 4000, "y" * 4000)  # newest, bigger than the whole budget

    inputHandler.lambda_handler(VALID_EVENT, None)

    assert fake.calls[0]["messages"] == [{"role": "user", "content": [{"text": "Hello"}]}]


def test_successful_chat_saves_one_exchange_with_ttl(monkeypatch, fake_table):
    monkeypatch.setattr(inputHandler, "bedrock", FakeBedrock(response=good_reply("Hi there!")))

    inputHandler.lambda_handler(VALID_EVENT, None)

    assert len(fake_table.put_calls) == 1
    item = fake_table.put_calls[0]
    assert item["pk"] == "user-1#abc-123"
    assert item["userText"] == "Hello"
    assert item["assistantText"] == "Hi there!"
    assert isinstance(item["ts"], int) and isinstance(item["expiresAt"], int)
    assert abs(item["ts"] / 1000 - time.time()) < 5                      # milliseconds
    assert abs(item["expiresAt"] - (time.time() + 7 * 24 * 3600)) < 5    # seconds, 7 days out


def test_failed_bedrock_call_saves_nothing(monkeypatch, fake_table):
    monkeypatch.setattr(inputHandler, "bedrock", FakeBedrock(error=RuntimeError("boom")))
    inputHandler.lambda_handler(VALID_EVENT, None)
    assert fake_table.put_calls == []


def test_malformed_bedrock_response_saves_nothing(monkeypatch, fake_table):
    monkeypatch.setattr(inputHandler, "bedrock", FakeBedrock(response={"output": {}}))
    inputHandler.lambda_handler(VALID_EVENT, None)
    assert fake_table.put_calls == []


def test_blank_reply_is_not_saved(monkeypatch, fake_table):
    monkeypatch.setattr(inputHandler, "bedrock", FakeBedrock(response=good_reply("   ")))
    inputHandler.lambda_handler(VALID_EVENT, None)
    assert fake_table.put_calls == []


def test_one_user_cannot_read_another_users_history(monkeypatch, fake_table):
    fake = FakeBedrock(response=good_reply())
    monkeypatch.setattr(inputHandler, "bedrock", fake)
    fake_table.add("user-2#abc-123", 1000, "user two's secret question", "user two's secret answer")

    inputHandler.lambda_handler(make_event({"sessionId": "abc-123", "message": "Hello"}, sub="user-1"), None)

    texts = [m["content"][0]["text"] for m in fake.calls[0]["messages"]]
    assert texts == ["Hello"]


def test_missing_user_claim_returns_401_and_touches_nothing(monkeypatch, fake_table):
    fake = FakeBedrock(response=good_reply())
    monkeypatch.setattr(inputHandler, "bedrock", fake)

    result = inputHandler.lambda_handler(make_event({"sessionId": "abc-123", "message": "Hello"}, sub=None), None)

    assert result["statusCode"] == 401
    assert fake.calls == []
    assert fake_table.query_calls == []
    assert fake_table.put_calls == []


def test_history_load_failure_still_answers(monkeypatch):
    monkeypatch.setattr(inputHandler, "table", FakeTable(fail_query=True))
    fake = FakeBedrock(response=good_reply("still here"))
    monkeypatch.setattr(inputHandler, "bedrock", fake)

    result = inputHandler.lambda_handler(VALID_EVENT, None)

    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == {"reply": "still here"}
    assert fake.calls[0]["messages"] == [{"role": "user", "content": [{"text": "Hello"}]}]


def test_history_save_failure_still_returns_the_reply(monkeypatch):
    monkeypatch.setattr(inputHandler, "table", FakeTable(fail_put=True))
    monkeypatch.setattr(inputHandler, "bedrock", FakeBedrock(response=good_reply("answer")))

    result = inputHandler.lambda_handler(VALID_EVENT, None)

    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == {"reply": "answer"}