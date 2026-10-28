import test from 'node:test';
import assert from 'node:assert/strict';
import {resetRule,targets,rulePayload} from '../../01_可运行代码/web/rules-state.mjs';
import {nextQuestion} from '../../01_可运行代码/web/fill-state.mjs';

const questions=[
  {id:'s',type:'single',options:[{id:'a'},{id:'b'}]},
  {id:'m',type:'multiple',options:[{id:'a'},{id:'b'}]},
  {id:'n',type:'number'},{id:'t',type:'text'},{id:'last',type:'number'},
];
const rule=(source,operator,value,target='END') => ({source_question_id:source,operator,value,target_question_id:target});
test('source changes reset typed conditions and expose only forward targets',() => {
  assert.equal(resetRule(questions,'s').value,'a');
  assert.deepEqual(resetRule(questions,'m').value,[]);
  assert.equal(resetRule(questions,'t').value,'');
  assert.equal(resetRule(questions,'last').target_question_id,'END');
  assert.deepEqual(targets(questions,'n').map(q=>q.id),['t','last']);
});
test('rule editor serializes stable IDs, selected options, trimmed text and numeric zero',() => {
  const input=[rule('s','eq','a','n'),rule('m','contains_all',['a','b']),rule('n','between',['0','18']),rule('t','contains',' 北京 ')];
  const saved=structuredClone(input), result=rulePayload(questions,input);
  assert.deepEqual(result,[rule('s','eq','a','n'),rule('m','contains_all',['a','b']),rule('n','between',[0,18]),rule('t','contains','北京')]);
  assert.deepEqual(input,saved);
});
test('empty, invalid type, duplicate option, impossible target and numeric overflow cannot save',() => {
  for (const item of [rule('s','eq','missing'),rule('m','contains_any',[]),rule('m','contains_any',['a','a']),
    rule('t','eq',' '),rule('t','gt','x'),rule('n','eq',''),rule('n','eq',true),rule('n','eq','1e999'),
    rule('n','between',['18','0']),rule('n','gt','2','s'),rule('missing','eq',1),rule('n','eq','9007199254740992')]) {
    assert.throws(()=>rulePayload(questions,[item]));
  }
});
test('text equals/contains respect normalized text and first matching priority',() => {
  const rules=[rule('t','contains','北京','last'),rule('t','eq','北京','END')];
  assert.equal(nextQuestion(questions,rules,'t','北京'),'last');
  assert.equal(nextQuestion(questions,[...rules].reverse(),'t','北京'),'END');
  assert.equal(nextQuestion(questions,[rule('t','eq',' 北京 ')],'t','北京'),'END');
  assert.equal(nextQuestion(questions,rules,'t','上海'),'last');
  assert.equal(nextQuestion(questions,rules,'t',null),'last');
});
