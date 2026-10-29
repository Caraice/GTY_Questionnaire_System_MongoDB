from copy import deepcopy

import pytest

from app.domain.routing import validate_rules


def questions():
    return [{'id':'s','type':'single','options':[{'id':'a'},{'id':'b'}]},
            {'id':'m','type':'multiple','options':[{'id':'a'},{'id':'b'}]},
            {'id':'n','type':'number'}, {'id':'t','type':'text'}]


@pytest.mark.parametrize('source,op,value', [('s','eq','a'),('m','contains_any',['a']),('m','contains_all',['a','b']),
    ('n','eq',0),('n','gt',1.5),('n','gte',0),('n','lt',100),('n','lte',120),('n','between',[0,120]),
    ('t','eq','北京'),('t','contains','北京')])
def test_valid_rule_conditions(source,op,value):
    validate_rules(questions(),[{'source_question_id':source,'operator':op,'value':value,'target_question_id':'END'}])


@pytest.mark.parametrize('source,op,value,target', [
    ('missing','eq','a','END'),('s','eq','a','missing'),('s','eq','a','s'),('n','gte',0,'s'),
    ('s','eq','missing','END'),('s','gt',1,'END'),('s','eq',True,'END'),
    ('m','contains_any',[],'END'),('m','contains_any',['a','a'],'END'),('m','contains_all',['missing'],'END'),
    ('m','eq','a','END'),('m','contains_any',[True],'END'),('n','gte',True,'END'),('n','gte','0','END'),
    ('n','gte',float('inf'),'END'),('n','eq',float('nan'),'END'),('n','between',[1,0],'END'),
    ('n','between',[0],'END'),('n','between',[0,True],'END'),('t','gt','x','END'),
    ('t','eq','  ','END'),('t','contains',0,'END'),('t','contains',[],'END'),
    ('s','contains','a','END'),('m','contains','a','END'),('n','contains','0','END'),
])
def test_invalid_rule_conditions(source,op,value,target):
    with pytest.raises(ValueError):
        validate_rules(questions(),[{'source_question_id':source,'operator':op,'value':value,'target_question_id':target}])


def test_rules_allow_empty_and_forward_target_but_reserve_end():
    validate_rules(questions(),[])
    validate_rules(questions(),[{'source_question_id':'s','operator':'eq','value':'a','target_question_id':'n'}])
    invalid = deepcopy(questions()); invalid[0]['id'] = 'END'
    with pytest.raises(ValueError): validate_rules(invalid,[])
