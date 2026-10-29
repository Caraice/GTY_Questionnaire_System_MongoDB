from copy import deepcopy

from app.domain.version_diff import compare_content


def test_option_identity_distinguishes_reorder_from_relabel_and_add_remove():
    before = {'title':'题干','type':'single','required':False,'options':[
        {'id':'a','label':'相同文字'},{'id':'b','label':'相同文字'}],'validation':{}}
    after = deepcopy(before); after['options'].reverse()
    diff = compare_content(before, after)
    assert diff['options'] == [{'field':'order','before':['a','b'],'after':['b','a']}]
    after['options'] = [{'id':'b','label':'改名'},{'id':'c','label':'新增'}]
    changes = {x['field']:x for x in compare_content(before,after)['options']}
    assert changes['a']['after'] is None and changes['b']['before']=='相同文字' and changes['b']['after']=='改名'
    assert changes['c']['before'] is None and changes['c']['after']=='新增'
    assert before['options'][0]['id']=='a'


def test_zero_limits_and_false_required_are_changes_rather_than_missing_values():
    before={'title':'旧题','type':'number','required':True,'validation':{'minimum':None,'integer_only':False}}
    after={'title':'新题','type':'number','required':False,'validation':{'minimum':0,'integer_only':True}}
    diff=compare_content(before,after)
    assert diff['validation']==[{'field':'minimum','before':None,'after':0},
                                {'field':'integer_only','before':False,'after':True}]
    assert diff['other']==[{'field':'required','before':True,'after':False}]
    assert diff['title']==[{'field':'title','before':'旧题','after':'新题'}]
