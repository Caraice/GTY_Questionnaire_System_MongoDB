import pytest
from pydantic import TypeAdapter, ValidationError

from app.models import Question, QuestionSet, SurveyCreate
from app.services.auth import hash_password, verify_password


@pytest.mark.parametrize("data", [
    {"id": "q1", "type": "single", "title": "单选", "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]},
    {"id": "q2", "type": "multiple", "title": "多选", "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}], "validation": {"exact_choices": 2}},
    {"id": "q3", "type": "text", "title": "文本", "validation": {"min_length": 2, "max_length": 10}},
    {"id": "q4", "type": "number", "title": "年龄", "validation": {"minimum": 0, "maximum": 120, "integer_only": True}},
])
def test_four_question_contracts(data):
    assert TypeAdapter(Question).validate_python(data).id == data["id"]


@pytest.mark.parametrize("validation", [
    {"min_length": 4, "max_length": 3},
    {"min_length": -1},
])
def test_bad_text_constraints(validation):
    with pytest.raises(ValidationError):
        TypeAdapter(Question).validate_python({"id": "q", "type": "text", "title": "T", "validation": validation})


@pytest.mark.parametrize("validation", [
    {"minimum": 20, "maximum": 10}, {"minimum": float("nan")}, {"maximum": float("inf")},
])
def test_bad_numeric_constraints(validation):
    with pytest.raises(ValidationError):
        TypeAdapter(Question).validate_python({"id": "q", "type": "number", "title": "N", "validation": validation})


@pytest.mark.parametrize("validation", [
    {"min_choices": 3}, {"exact_choices": 1, "min_choices": 0}, {"min_choices": 2, "max_choices": 1},
])
def test_bad_choice_constraints(validation):
    with pytest.raises(ValidationError):
        TypeAdapter(Question).validate_python({"id": "q", "type": "multiple", "title": "M", "options": [
            {"id": "a", "label": "A"}, {"id": "b", "label": "B"}], "validation": validation})


def test_duplicate_question_ids_rejected():
    q = {"id": "q", "type": "text", "title": "T"}
    with pytest.raises(ValidationError):
        QuestionSet(expected_version=1, questions=[q, q])


def test_unknown_payload_fields_rejected():
    with pytest.raises(ValidationError):
        SurveyCreate(title="T", owner_id="forged")


def test_blank_title_rejected():
    with pytest.raises(ValidationError):
        SurveyCreate(title="   ")


def test_naive_deadline_rejected():
    with pytest.raises(ValidationError):
        SurveyCreate(title="T", deadline="2027-01-01T00:00:00")


def test_password_salted_and_verified():
    first, second = hash_password("test-pass-123"), hash_password("test-pass-123")
    assert first != second
    assert verify_password("test-pass-123", first)
    assert not verify_password("wrong-pass", first)
    assert not verify_password("test-pass-123", "invalid")

