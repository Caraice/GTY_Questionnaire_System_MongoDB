import test from 'node:test';
import assert from 'node:assert/strict';
import {parseNumber,normalizeAnswer,nextQuestion,answerPath,saveAnswer} from '../../01_可运行代码/web/fill-state.mjs';

const questions = [
  {id:'s',type:'single',required:true,options:[{id:'a'},{id:'b'}]},
  {id:'m',type:'multiple',options:[{id:'a'},{id:'b'}],validation:{exact_choices:2}},
  {id:'n',type:'number',validation:{minimum:0,maximum:120,integer_only:true}},
  {id:'t',type:'text',required:true,validation:{min_length:2,max_length:4}},
];
const rule = (source,operator,value,target) => ({source_question_id:source,operator,value,target_question_id:target});

test('zero stays numeric, empty is omission, and invalid or unsafe numbers are rejected',() => {
  assert.equal(parseNumber('0'),0); assert.equal(parseNumber(''),null);
  assert.equal(normalizeAnswer(questions[2],parseNumber('0')),0);
  for (const value of ['Infinity','NaN','1e999','0x10']) assert.throws(() => parseNumber(value));
  for (const value of [true,'0',-1,121,1.5,Number.MAX_SAFE_INTEGER+1]) assert.throws(() => normalizeAnswer(questions[2],value));
});
test('required choices, duplicate choices, count limits and Unicode length match the server',() => {
  assert.throws(() => normalizeAnswer(questions[0],null));
  assert.throws(() => normalizeAnswer(questions[1],['a']));
  assert.throws(() => normalizeAnswer(questions[1],['a','a']));
  assert.deepEqual(normalizeAnswer(questions[1],['a','b']),['a','b']);
  assert.equal(normalizeAnswer(questions[3],' \u3000中文\u3000 '),'中文');
  assert.equal(normalizeAnswer(questions[3],'😀😀'),'😀😀');
  assert.throws(() => normalizeAnswer(questions[3],' '));
});
test('single/multiple/numeric routes use first match, inclusive bounds and default order',() => {
  assert.equal(nextQuestion(questions,[rule('s','eq','a','n'),rule('s','eq','a','END')],'s','a'),'n');
  assert.equal(nextQuestion(questions,[],'s','b'),'m');
  assert.equal(nextQuestion(questions,[rule('m','contains_any',['b'],'END')],'m',['a','b']),'END');
  assert.equal(nextQuestion(questions,[rule('m','contains_all',['a','b'],'END')],'m',['a']),'n');
  assert.equal(nextQuestion(questions,[rule('n','between',[0,18],'END')],'n',0),'END');
  assert.equal(nextQuestion(questions,[rule('n','gte',0,'END')],'n',null),'t');
  assert.throws(() => nextQuestion(questions,[rule('n','gte',0,'s')],'n',0));
});
test('changing earlier answers removes skipped data and keeps stable IDs without mutating input',() => {
  const rules=[rule('s','eq','a','n')], original={s:'b',m:['a','b'],n:0,t:'完成'};
  const result=saveAnswer(questions,rules,original,'s','a');
  assert.deepEqual(result.answers,{s:'a',n:0,t:'完成'});
  assert.deepEqual(result.path,['s','n','t']); assert.equal(result.complete,true);
  assert.deepEqual(original,{s:'b',m:['a','b'],n:0,t:'完成'});
  const ended=saveAnswer(questions,[rule('s','eq','a','END')],original,'s','a');
  assert.deepEqual(ended.answers,{s:'a'});
});
test('visited optional omissions advance but unanswered questions stop the preview',() => {
  assert.deepEqual(answerPath(questions,[],{s:'b'}),{path:['s','m'],complete:false});
  assert.deepEqual(answerPath(questions,[],{s:'b',m:null,n:0,t:'完成'}),{path:['s','m','n','t'],complete:true});
});
