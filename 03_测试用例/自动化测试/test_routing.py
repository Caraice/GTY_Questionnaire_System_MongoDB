from copy import deepcopy

import pytest

from app.domain.routing import PathValidationError, next_question, validate_path


def questions():
    return [
        {"id":"s","type":"single","required":True,"options":[{"id":"a"},{"id":"b"}]},
        {"id":"m","type":"multiple","options":[{"id":"a"},{"id":"b"},{"id":"c"}]},
        {"id":"n","type":"number","validation":{"minimum":0,"maximum":120,"integer_only":True}},
        {"id":"t","type":"text","required":True,"validation":{"min_length":2,"max_length":8}},
    ]


def rule(source, operator, value, target):
    return {"source_question_id":source,"operator":operator,"value":value,"target_question_id":target}


@pytest.mark.parametrize('source,operator,value,answer,matched',[
    ('s','eq','a','a',True),('s','eq','a','b',False),
    ('m','contains_any',['a','b'],['b'],True),('m','contains_any',['a','b'],['c'],False),
    ('m','contains_all',['a','b'],['b','a','c'],True),('m','contains_all',['a','b'],['a'],False),
    ('m','contains_all',['a'],[],False),('n','gte',0,0,True),('n','gt',0,0,False),
    ('n','lte',0,0,True),('n','lt',1,0,True),('n','eq',0,0.0,True),
    ('n','between',[0,18],0,True),('n','between',[0,18],18,True),('n','between',[0,18],19,False),
    ('n','gte',0,None,False),
])
def test_routing_operators_and_boundaries(source,operator,value,answer,matched):
    definitions=questions()
    expected='END' if matched else {'s':'m','m':'n','n':'t'}[source]
    assert next_question(definitions,[rule(source,operator,value,'END')],source,answer)==expected


def test_first_matching_rule_default_order_end_and_no_mutation():
    definitions=questions()
    rules=[rule('s','eq','a','n'),rule('s','eq','a','END')]
    assert next_question(definitions,rules,'s','a')=='n'
    assert next_question(definitions,[],'t','完成')=='END'
    answers={'s':'a','n':0,'t':' 完成 '}
    original=deepcopy((definitions,rules,answers))
    assert validate_path(definitions,rules,answers)==(['s','n','t'],{'s':'a','n':0,'t':'完成'})
    assert (definitions,rules,answers)==original


def test_skipped_required_questions_are_not_validated_and_omissions_are_removed():
    definitions=questions(); definitions[1]['required']=True
    assert validate_path(definitions,[rule('s','eq','a','END')],{'s':'a'})==(['s'],{'s':'a'})
    definitions[1]['required']=False
    assert validate_path(definitions,[],{'s':'a','m':[],'n':None,'t':'完成'})==(
        ['s','m','n','t'],{'s':'a','t':'完成'})


@pytest.mark.parametrize('answers,qid',[
    ({'s':'a','unknown':1},'unknown'),({'s':'a','t':None},'t'),({'s':'a','n':0},'n'),
])
def test_unknown_and_outside_answers_rejected_even_if_empty(answers,qid):
    with pytest.raises(PathValidationError) as error:
        validate_path(questions(),[rule('s','eq','a','END')],answers)
    assert error.value.question_id==qid


@pytest.mark.parametrize('answers,qid',[
    ({},'s'),({'s':'b'},'t'),({'s':'b','n':True,'t':'完成'},'n'),
    ({'s':'b','n':'0','t':'完成'},'n'),({'s':'b','m':['a','a'],'t':'完成'},'m'),
])
def test_visited_answers_are_strictly_validated(answers,qid):
    with pytest.raises(PathValidationError) as error:
        validate_path(questions(),[],answers)
    assert error.value.question_id==qid


def test_invalid_graph_empty_survey_and_unknown_current_fail_closed():
    with pytest.raises(ValueError): validate_path(questions(),[rule('n','gte',0,'s')],{'s':'a'})
    with pytest.raises(ValueError): validate_path([],[],{})
    with pytest.raises(ValueError): next_question(questions(),[],'missing',None)
