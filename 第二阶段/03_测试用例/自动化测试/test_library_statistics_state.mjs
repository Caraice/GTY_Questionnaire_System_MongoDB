import test from 'node:test';
import assert from 'node:assert/strict';
import {mergePayload,metricLabels,versionLabel} from '../../01_可运行代码/web/library-statistics-state.mjs';
import {questionPayload} from '../../01_可运行代码/web/editor-state.mjs';

const versions=[{id:'v1',number:1,content:{type:'single',title:'同名选项题'}},{id:'v2',number:2,content:{type:'single',title:'同名选项题'}}];
test('explicit mapping uses stable IDs and never derives mappings from labels',()=>{
  const payload=mergePayload(versions,new Set(['v1','v2']),'v1',{'v2':{'x':'b','y':'a'}});
  assert.deepEqual(payload.option_mappings,{'v2':{'x':'b','y':'a'}});
  assert.deepEqual(mergePayload(versions,new Set(['v1','v2']),'v1').option_mappings,{});
});
test('only selected versions and nonblank unit confirmations enter merge request',()=>{
  const payload=mergePayload(versions,new Set(['v1']),'v1',{'v2':{'x':'a'}},{v1:' kg ',v2:'g'});
  assert.deepEqual(payload.version_ids,['v1']);assert.deepEqual(payload.unit_confirmations,{v1:'kg'});assert.deepEqual(payload.option_mappings,{});
});
test('metrics preserve numeric zero and distinguish questionnaire response and answer counts',()=>{
  assert.deepEqual(metricLabels({survey_count:2,response_count:3,answer_count:0}),['涉及问卷数：2','答卷数：3','题目回答次数：0']);
  assert.match(versionLabel(versions[1]),/^v2 · 单选题/);
});
test('unit round-trips without adding an undefined field to historical question payloads',()=>{
  const q={id:'q',type:'number',title:'重量',required:false,validation:{minimum:0}};
  assert.equal('unit' in questionPayload(q),false);
  assert.equal(questionPayload({...q,unit:'kg'}).unit,'kg');
  assert.equal(questionPayload({...q,type:'text',unit:'kg'}).unit,undefined);
});
