"""Real MongoDB aggregation checked against independently hand-counted answers.

Fixtures insert persisted documents into isolated databases to test aggregation
and malformed history independently. test_response_api covers real POST -> statistics.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from itertools import count
from sys import float_info
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pymongo.collection import Collection

from app.config import Settings
from app.main import create_app

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.getenv("RUN_MONGO_TESTS") != "1", reason="Requires real MongoDB")]

QUESTION_IDS = ["single", "multiple", "text", "number"]
FIXTURE_SEQUENCE = count()


@pytest.fixture
def ctx():
    settings = Settings(os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27019"), "survey_test_" + uuid4().hex)
    with TestClient(create_app(settings)) as client:
        auth = {}
        for name in ["Owner", "Other"]:
            credentials = {"username": name, "password": "Test-pass-123"}
            assert client.post("/api/auth/register", json=credentials).status_code == 201
            auth[name] = {"Authorization": "Bearer " + client.post("/api/auth/login", json=credentials).json()["access_token"]}
        survey = client.post("/api/surveys", headers=auth["Owner"], json={"title": "手算统计核对"}).json()
        options = [{"id": letter, "label": "选项" + letter} for letter in "ABC"]
        questions = [
            {"id": "single", "type": "single", "title": "单选", "options": options},
            {"id": "multiple", "type": "multiple", "title": "多选", "options": options},
            {"id": "text", "type": "text", "title": "建议", "validation": {"min_length": 1, "max_length": 20}},
            {"id": "number", "type": "number", "title": "数字", "validation": {"minimum": 0, "maximum": 120}},
        ]
        base = f'/api/surveys/{survey["id"]}'
        saved = client.put(base + "/questions", headers=auth["Owner"], json={"expected_version": 1, "questions": questions})
        assert saved.status_code == 200, saved.text
        published = client.post(base + "/publish", headers=auth["Owner"], json={"expected_version": 2})
        assert published.status_code == 200, published.text
        yield client, auth, published.json()
    print(f"\nRetained statistics test database: {settings.mongodb_db}")


def insert(ctx, answers, *, path=None, survey_id=None, version=None, timestamp=None, doc_id=None):
    client, _, survey = ctx
    identity = uuid4().hex
    client.app.state.db.responses.insert_one({
        "_id": doc_id or identity, "schema_version": 1,
        "survey_id": survey_id or survey["id"],
        "survey_version": version if version is not None else survey["published_version"],
        # Deliberately the same respondent: the total is submissions, not unique accounts.
        "respondent_key": "repeat-respondent", "attempt_key": identity, "submission_key": identity,
        "anonymous": True, "answers": answers, "path": QUESTION_IDS if path is None else path,
        # BSON dates have millisecond precision; avoid accidental random UUID
        # tie ordering in fixtures that expect the insertion sequence.
        "submitted_at": timestamp or datetime(2026, 1, 2, tzinfo=timezone.utc) + timedelta(milliseconds=next(FIXTURE_SEQUENCE)),
    })


def stats(ctx, question_id=None):
    client, auth, survey = ctx
    result = client.get(f'/api/surveys/{survey["id"]}/statistics', headers=auth["Owner"],
                        params={} if question_id is None else {"question_id": question_id})
    assert result.status_code == 200, result.text
    return result.json()


def by_id(result):
    return {question["id"]: question for question in result["questions"]}


def counts(question):
    return {option["id"]: option["count"] for option in question["options"]}


def hand_data(ctx):
    rows = [
        ({"single": "A", "multiple": ["A", "B"], "text": "优秀", "number": 0}, QUESTION_IDS),
        ({"single": "A", "multiple": ["A"], "text": "待改进", "number": 10}, QUESTION_IDS),
        ({"single": "B", "multiple": ["B", "C"], "number": 20}, ["single", "multiple", "number"]),
        ({"multiple": [], "text": "0", "number": 0}, ["multiple", "text", "number"]),
        ({"single": None, "text": " \t", "number": None}, ["single", "text", "number"]),
        ({}, []),
    ]
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index, (answers, path) in enumerate(rows):
        insert(ctx, answers, path=path, timestamp=start + timedelta(seconds=index))


def test_hand_calculation_and_single_question_consistency(ctx, monkeypatch):
    hand_data(ctx)
    # Verify the real endpoint uses aggregate rather than retrieving raw responses.
    original_find, original_aggregate = Collection.find, Collection.aggregate
    pipelines = []

    def guarded_find(collection, *args, **kwargs):
        assert collection.name != "responses", "Statistics must not load response documents into Python"
        return original_find(collection, *args, **kwargs)

    def recorded_aggregate(collection, pipeline, *args, **kwargs):
        if collection.name == "responses":
            pipelines.append(pipeline)
        return original_aggregate(collection, pipeline, *args, **kwargs)

    monkeypatch.setattr(Collection, "find", guarded_find)
    monkeypatch.setattr(Collection, "aggregate", recorded_aggregate)
    result = stats(ctx)
    assert result["total_submissions"] == 6
    questions = by_id(result)
    assert list(questions) == QUESTION_IDS
    assert questions["single"]["answered_count"] == 3
    assert counts(questions["single"]) == {"A": 2, "B": 1, "C": 0}
    assert questions["multiple"]["answered_count"] == 3
    assert questions["multiple"]["selection_count"] == 5
    assert counts(questions["multiple"]) == {"A": 2, "B": 2, "C": 1}
    assert questions["text"]["answered_count"] == 3
    assert questions["text"]["values"] == ["优秀", "待改进", "0"]
    assert questions["number"]["answered_count"] == 4
    assert questions["number"]["values"] == [0, 10, 20, 0]
    assert questions["number"]["average"] == 7.5  # (0 + 10 + 20 + 0) / 4
    for question_id in QUESTION_IDS:
        single = stats(ctx, question_id)
        assert single == {**result, "questions": [questions[question_id]]}
    assert len(pipelines) == 5 and all("$facet" in pipeline[-1] for pipeline in pipelines)
    serialized = json.dumps(result, ensure_ascii=False, allow_nan=False)
    assert all(private not in serialized for private in ["respondent_key", "submission_key", "repeat-respondent"])
    print("\nMongoDB actual hand-check result: " + serialized)


def test_empty_results_and_zero_are_distinct_and_recomputed(ctx):
    empty = stats(ctx)
    assert empty["total_submissions"] == 0
    assert all(question["answered_count"] == 0 for question in empty["questions"])
    assert counts(by_id(empty)["single"]) == {"A": 0, "B": 0, "C": 0}
    assert by_id(empty)["number"]["average"] is None
    assert by_id(empty)["text"]["values"] == []
    insert(ctx, {"number": 0})
    insert(ctx, {"number": 0.0})
    insert(ctx, {})
    result = stats(ctx)
    assert result["total_submissions"] == 3
    assert by_id(result)["number"]["answered_count"] == 2
    assert by_id(result)["number"]["average"] == 0
    insert(ctx, {"number": 12})
    assert by_id(stats(ctx))["number"]["average"] == 4


def test_skipped_stale_answers_other_surveys_and_versions_are_excluded(ctx):
    insert(ctx, {"number": 0, "single": "A", "multiple": ["A"], "text": "stale"}, path=[])
    insert(ctx, {"number": 100}, survey_id="another-survey")
    insert(ctx, {"number": 100}, version=ctx[2]["published_version"] - 1)
    insert(ctx, {"number": 0}, path=["number"])
    result = stats(ctx)
    assert result["total_submissions"] == 2
    assert [question["answered_count"] for question in result["questions"]] == [0, 0, 0, 1]
    assert by_id(result)["number"]["values"] == [0]


def test_malformed_historical_answers_never_coerce_or_crash(ctx):
    bad_numbers = [True, False, "0", "12", [], {}, float("nan"), float("inf"), -float("inf"), -1, 121]
    bad_multiple = ["A", True, {}, ["A", "A"], ["A", "unknown"], [1], [["A"]], None, []]
    bad_single = [True, 1, ["A"], "unknown", " A ", "", None, {}]
    bad_text = [True, 0, [], {}, " \t\u3000\x1c", "x" * 21, None]
    for index in range(len(bad_numbers)):
        insert(ctx, {"number": bad_numbers[index], "multiple": bad_multiple[index % len(bad_multiple)],
                     "single": bad_single[index % len(bad_single)], "text": bad_text[index % len(bad_text)]})
    insert(ctx, {"single": "A", "multiple": ["A", "B"], "text": " \u3000零\u3000 ", "number": 0})
    result = stats(ctx)
    assert result["total_submissions"] == 12
    assert [question["answered_count"] for question in result["questions"]] == [1, 1, 1, 1]
    assert by_id(result)["text"]["values"] == ["零"]
    assert by_id(result)["number"]["values"] == [0]
    assert by_id(result)["number"]["average"] == 0


def test_question_constraints_are_applied_in_aggregation(ctx):
    # Simulate historical data under a frozen definition with stricter constraints.
    db = ctx[0].app.state.db
    db.surveys.update_one({"_id": ctx[2]["id"]}, {"$set": {
        "questions.1.validation": {"min_choices": 2, "max_choices": 2, "exact_choices": 2},
        "questions.2.validation": {"min_length": 2, "max_length": 3},
        "questions.3.validation": {"minimum": -2, "maximum": 2, "integer_only": True},
    }})
    for multiple, text, number in [(["A"], "a", -3), (["A", "B", "C"], "abcd", 3),
                                    (["A", "C"], "中文", 1.5), (["A", "B"], " 零值 ", -2.0),
                                    ([], "", 0), (None, None, 2)]:
        insert(ctx, {"multiple": multiple, "text": text, "number": number})
    questions = by_id(stats(ctx))
    assert questions["multiple"]["answered_count"] == 2
    assert questions["multiple"]["selection_count"] == 4
    assert counts(questions["multiple"]) == {"A": 2, "B": 1, "C": 1}
    assert questions["text"]["values"] == ["中文", "零值"]
    assert questions["number"]["values"] == [-2.0, 0, 2]
    assert questions["number"]["average"] == 0


def test_large_finite_numbers_do_not_overflow_average(ctx):
    ctx[0].app.state.db.surveys.update_one({"_id": ctx[2]["id"]}, {"$set": {"questions.3.validation": {}}})
    insert(ctx, {"number": float_info.max})
    insert(ctx, {"number": float_info.max})
    assert by_id(stats(ctx))["number"]["average"] == float_info.max
    insert(ctx, {"number": -float_info.max})
    assert by_id(stats(ctx))["number"]["average"] == pytest.approx(float_info.max / 3)


def test_content_version_survives_management_changes(ctx):
    client, auth, survey = ctx
    insert(ctx, {"number": 0})
    before = stats(ctx)
    base = f'/api/surveys/{survey["id"]}'
    updated = client.patch(base + "/settings", headers=auth["Owner"], json={
        "expected_version": survey["version"], "deadline": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()})
    assert updated.status_code == 200
    assert stats(ctx) == before
    client.app.state.db.surveys.update_one({"_id": survey["id"]}, {"$set": {"deadline": datetime(2000, 1, 1, tzinfo=timezone.utc)}})
    assert stats(ctx) == before  # expired surveys remain viewable by owner
    closed = client.post(base + "/close", headers=auth["Owner"], json={"expected_version": updated.json()["version"]})
    assert closed.status_code == 200
    assert stats(ctx) == before


def test_auth_unknown_question_and_missing_survey_do_not_aggregate(ctx, monkeypatch):
    client, auth, survey = ctx

    def forbidden(*args, **kwargs):
        pytest.fail("Unauthorized or unknown resources must be rejected before aggregating")

    monkeypatch.setattr(Collection, "aggregate", forbidden)
    endpoint = f'/api/surveys/{survey["id"]}/statistics'
    assert client.get(endpoint).status_code == 401
    assert client.get(endpoint, headers=auth["Other"]).status_code == 404
    assert client.get(endpoint, headers=auth["Other"], params={"question_id": "number"}).status_code == 404
    for question_id in ["unknown", "$number", "", "number.x"]:
        assert client.get(endpoint, headers=auth["Owner"], params={"question_id": question_id}).status_code == 404
    assert client.get("/api/surveys/missing/statistics", headers=auth["Owner"]).status_code == 404


def test_draft_without_questions_and_legacy_published_version(ctx):
    client, auth, survey = ctx
    draft = client.post("/api/surveys", headers=auth["Owner"], json={"title": "空草稿"}).json()
    result = client.get(f'/api/surveys/{draft["id"]}/statistics', headers=auth["Owner"])
    assert result.status_code == 200
    assert result.json() == {"survey_id": draft["id"], "survey_version": 1, "total_submissions": 0, "questions": []}
    insert(ctx, {"number": 0})
    before = stats(ctx)
    client.app.state.db.surveys.update_one({"_id": survey["id"]}, {"$unset": {"published_version": ""}})
    assert stats(ctx) == before


def test_values_have_stable_submission_order(ctx):
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    insert(ctx, {"text": "second", "number": 2}, timestamp=timestamp, doc_id="b")
    insert(ctx, {"text": "first", "number": 1}, timestamp=timestamp, doc_id="a")
    for _ in range(2):
        questions = by_id(stats(ctx))
        assert questions["text"]["values"] == ["first", "second"]
        assert questions["number"]["values"] == [1, 2]
