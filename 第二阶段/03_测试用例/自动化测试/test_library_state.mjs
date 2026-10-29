import test from 'node:test';
import assert from 'node:assert/strict';
import {previewText, sourceText} from '../../01_可运行代码/web/library-state.mjs';
import {questionPayload, moveItem} from '../../01_可运行代码/web/editor-state.mjs';

test('preview displays the chosen version content, including zero limits', () => {
  const v1 = {number:1, content:{type:'number', title:'旧题', required:true, validation:{minimum:0}}};
  const v2 = {number:2, content:{...v1.content, title:'新题'}};
  assert.match(previewText(v1), /版本 1.*数字填空/);
  assert.match(previewText(v1), /最小值 0/);
  assert.match(previewText(v2), /新题/);
  assert.doesNotMatch(previewText(v2), /旧题/);
  assert.match(previewText(null), /请选择/);
});

test('duplicate imports retain independent instance and option IDs on reorder/save', () => {
  const content = {type:'single', title:'同一题', required:false, validation:{}, options:[{id:'a',label:'甲'},{id:'b',label:'乙'}]};
  const first = {id:'instance-1', ...structuredClone(content)}, second = {id:'instance-2', ...structuredClone(content)};
  const reordered = moveItem([first, second], second.id, -1).map(questionPayload);
  assert.deepEqual(reordered.map(q => q.id), ['instance-2', 'instance-1']);
  assert.deepEqual(reordered[0].options, reordered[1].options);
  reordered[0].title = '当前副本';
  assert.equal(first.title, '同一题');
});

test('provenance distinguishes origin version from local modifications', () => {
  const source = {template_id:'same-template', version_number:3, modified:false};
  assert.match(sourceText(source), /版本 3.*与来源版本一致/);
  assert.match(sourceText({...source, modified:true}), /已在本问卷修改/);
});
