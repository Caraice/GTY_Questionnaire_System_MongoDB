"""Boundary and wrong-type regressions for model -> per-question validation."""
import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.dependencies import current_user
from app.db import get_db
from app.domain.validation import validate_answer
from app.main import create_app
from app.models import Submission


def question(kind, required=False, **rules):
    result = {"id":"q", "type":kind, "title":"测试", "required":required, "validation":rules}
    if kind in {"single", "multiple"}:
        result["options"] = [{"id":key, "label":key} for key in ["a","b","c","d","0"]]
    return result


def submission(value):
    return Submission.model_validate({"survey_version":1, "submission_key":"test-key-1234567", "answers":{"q":value}})


@pytest.mark.parametrize('value', [True,False,0,0.0,12,12.5,'12','0','NaN',None,[True,0,'0'],{'nested':[False,1,1.0]}])
def test_submission_preserves_types_from_json(value):
    model = Submission.model_validate_json(json.dumps({
        "survey_version":1, "submission_key":"test-key-1234567", "answers":{"q":value},
    }))
    # Equality alone is insufficient: Python considers True == 1 and 1 == 1.0.
    assert json.dumps(model.answers['q']) == json.dumps(value)
    assert type(model.answers['q']) is type(value)


@pytest.mark.parametrize('value', [float('nan'),float('inf'),float('-inf')])
@pytest.mark.parametrize('wrap', [lambda x:x, lambda x:[x], lambda x:{'nested':x}])
def test_submission_rejects_nonfinite_at_any_depth(value, wrap):
    with pytest.raises(ValidationError):
        submission(wrap(value))


@pytest.mark.parametrize('kind,value', [
    ('single',None),('single',''),('single',' \t\u3000'),
    ('multiple',None),('multiple',[]),('text',None),('text',''),('text',' \n\u3000'),('number',None),
])
def test_required_and_optional_omissions(kind, value):
    with pytest.raises(ValueError, match='必答'):
        validate_answer(question(kind, required=True), value)
    # Optional omission does not activate minimum/exact constraints.
    rules = {'min_length':2} if kind == 'text' else {'exact_choices':2} if kind == 'multiple' else {}
    assert validate_answer(question(kind, **rules), value) is None


@pytest.mark.parametrize('kind,value', [
    ('single',False),('single',0),('single',['a']),('single',{}),
    ('multiple',False),('multiple',0),('multiple',''),('multiple',()),('multiple',{}),
    ('multiple',[True]),('multiple',[0]),('multiple',[None]),('multiple',[['a']]),
    ('text',False),('text',0),('text',[]),('text',{}),
    ('number',True),('number',False),('number','0'),('number',''),('number',' '),('number',[]),('number',{}),
])
def test_wrong_type_is_never_an_optional_omission(kind, value):
    with pytest.raises(ValueError):
        validate_answer(question(kind), value)


@pytest.mark.parametrize('value', ['a','0'])
def test_single_accepts_exact_option_id(value):
    assert validate_answer(question('single',required=True), value) == value


@pytest.mark.parametrize('value', ['missing',' a ','A'])
def test_single_does_not_normalize_invalid_id(value):
    with pytest.raises(ValueError):
        validate_answer(question('single'), value)


@pytest.mark.parametrize('value', [['a','a'],['a','missing'],['a',''],['a',' b ']])
def test_multiple_rejects_duplicates_and_unknown_options(value):
    with pytest.raises(ValueError):
        validate_answer(question('multiple'), value)


@pytest.mark.parametrize('rules,value,valid', [
    ({'min_choices':2,'max_choices':3},['a'],False),
    ({'min_choices':2,'max_choices':3},['a','b'],True),
    ({'min_choices':2,'max_choices':3},['a','b','c'],True),
    ({'min_choices':2,'max_choices':3},['a','b','c','d'],False),
    ({'exact_choices':2},['a'],False),({'exact_choices':2},['b','a'],True),
    ({'exact_choices':2},['a','b','c'],False),({'max_choices':0},['a'],False),
])
def test_multiple_choice_count_boundaries(rules, value, valid):
    if valid:
        result = validate_answer(question('multiple',required=True,**rules), value)
        assert result == value and result is not value  # preserve order, do not share mutable data
    else:
        with pytest.raises(ValueError):
            validate_answer(question('multiple',**rules), value)


@pytest.mark.parametrize('value,valid', [('中',False),('中文',True),('中文字符',True),('中文字符数',False),('  中文  ',True)])
def test_text_length_uses_trimmed_unicode_characters(value, valid):
    definition = question('text',required=True,min_length=2,max_length=4)
    if valid:
        assert validate_answer(definition,value) == value.strip()
    else:
        with pytest.raises(ValueError):
            validate_answer(definition,value)


def test_text_zero_string_is_an_answer():
    assert validate_answer(question('text',required=True,min_length=1,max_length=1),'0') == '0'


@pytest.mark.parametrize('value', [0,0.0,-0.0,120,120.0])
def test_numeric_inclusive_bounds_and_zero_are_valid(value):
    result = validate_answer(question('number',required=True,minimum=0,maximum=120,integer_only=True),value)
    assert result == value and type(result) is type(value)


@pytest.mark.parametrize('value', [-1,121,-0.5,120.5,1.5])
def test_numeric_range_and_integer_limits(value):
    with pytest.raises(ValueError):
        validate_answer(question('number',minimum=0,maximum=120,integer_only=True),value)


@pytest.mark.parametrize('value', [float('nan'),float('inf'),float('-inf')])
def test_numeric_direct_call_also_rejects_nonfinite(value):
    with pytest.raises(ValueError,match='有限'):
        validate_answer(question('number'),value)


def test_zero_bounds_are_not_ignored_and_decimals_can_be_allowed():
    assert validate_answer(question('number',minimum=0,maximum=0),0) == 0
    for value in [-0.1,0.1]:
        with pytest.raises(ValueError):
            validate_answer(question('number',minimum=0,maximum=0),value)
    assert validate_answer(question('number',minimum=-1,maximum=1),0.5) == 0.5


def test_large_integer_is_not_converted_to_float():
    value = 10 ** 400
    result = validate_answer(question('number',integer_only=True),value)
    assert result == value and type(result) is int
    with pytest.raises(ValueError):
        validate_answer(question('number',maximum=120),value)


def test_model_to_numeric_validation_does_not_turn_bool_into_zero_or_one():
    for value in [True,False,'0']:
        parsed = submission(value).answers['q']
        assert type(parsed) is type(value)
        with pytest.raises(ValueError):
            validate_answer(question('number',required=True,minimum=0,maximum=1),parsed)
    assert validate_answer(question('number',required=True),submission(0).answers['q']) == 0


def test_unknown_question_type_rejected_even_for_empty_answer():
    with pytest.raises(ValueError,match='题型'):
        validate_answer({'type':'unsupported'},None)


def test_validator_does_not_mutate_definition_or_answer():
    definition, answer = question('multiple',exact_choices=2), ['b','a']
    before = deepcopy((definition,answer))
    assert validate_answer(definition,answer) == ['b','a']
    assert (definition,answer) == before


@pytest.mark.parametrize('literal', ['NaN','Infinity','-Infinity','1e999'])
def test_http_nonfinite_returns_422_instead_of_serialization_error(literal):
    app = create_app()
    app.dependency_overrides[current_user] = lambda: {'_id':'test-user'}
    app.dependency_overrides[get_db] = lambda: None
    # No lifespan/DB: request validation and exception serialization happen
    # before the submission service is called.
    client = TestClient(app,raise_server_exceptions=False)
    body = '{"survey_version":1,"submission_key":"test-key-1234567","answers":{"q":' + literal + '}}'
    response = client.post('/api/fill/example/responses',content=body,headers={'Content-Type':'application/json'})
    assert response.status_code == 422, response.text
    assert response.json()['detail'][0]['loc'][:3] == ['body','answers','q']
