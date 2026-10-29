import test from 'node:test';
import assert from 'node:assert/strict';
import {createQuestion, createOption, changeType, moveItem, questionPayload} from '../../01_可运行代码/web/editor-state.mjs';

test('edits, question/option ordering and deletion preserve surviving IDs through save and reload', () => {
  const single = createQuestion('single'), multiple = createQuestion('multiple'), text = createQuestion('text'), number = createQuestion('number');
  const ids = [single, multiple, text, number].map(q => q.id);
  const optionIds = single.options.map(o => o.id);
  single.title = '修改题干'; single.options[0].label = '修改选项';
  single.options.push(createOption());
  single.options = moveItem(single.options, optionIds[0], 1);
  let items = moveItem([single, multiple, text, number], number.id, -1);
  items = items.filter(q => q.id !== text.id);
  const reloaded = JSON.parse(JSON.stringify(items.map(questionPayload)));
  assert.deepEqual(reloaded.map(q => q.id), [ids[0],ids[1],ids[3]]);
  assert.deepEqual(reloaded[0].options.slice(0,2).map(o => o.id), [optionIds[1],optionIds[0]]);
  assert.equal(new Set(ids).size, 4);
  assert.notEqual(single.options[2].id, optionIds[0]);
});

test('type switches preserve the question ID and choice IDs, without saving editor cache', () => {
  const question = createQuestion('single');
  const options = question.options;
  const multiple = changeType(question, 'multiple');
  const text = changeType(multiple, 'text');
  const back = changeType(text, 'single');
  assert.equal(back.id, question.id);
  assert.deepEqual(back.options, options);
  assert.equal(questionPayload(text).options, undefined);
  assert.equal(Object.hasOwn(questionPayload(text), 'choiceOptions'), false);
  assert.deepEqual(back.validation, {});
});

test('moving past either boundary leaves order and IDs intact', () => {
  const items = [createQuestion('text'),createQuestion('number')];
  assert.deepEqual(moveItem(items, items[0].id, -1), items);
  assert.deepEqual(moveItem(items, items[1].id, 1), items);
});
