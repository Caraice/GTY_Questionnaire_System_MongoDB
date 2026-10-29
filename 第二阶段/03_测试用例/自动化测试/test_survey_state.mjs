import test from 'node:test';
import assert from 'node:assert/strict';
import {surveyState,localDateTime} from '../../01_可运行代码/web/survey-state.mjs';

const deadline = '2030-01-01T00:00:00Z';
test('draft, published, expired and closed expose only their permitted actions', () => {
  const now = Date.parse(deadline)-1;
  const draft = surveyState({status:'draft',deadline},now);
  assert.equal(draft.label,'草稿'); assert.ok(draft.canEdit && draft.canPublish); assert.ok(!draft.canClose && !draft.canFill);
  const open = surveyState({status:'published',deadline},now);
  assert.equal(open.label,'已发布'); assert.ok(open.canClose && open.canFill && open.canSetDeadline); assert.ok(!open.canEdit && !open.canPublish);
  const expired = surveyState({status:'published',deadline},now+1);
  assert.equal(expired.label,'已截止'); assert.ok(!expired.canFill && expired.canClose && expired.canSetDeadline);
  const closed = surveyState({status:'closed',deadline},now);
  assert.equal(closed.label,'已关闭'); assert.ok(!closed.canEdit && !closed.canPublish && !closed.canSetDeadline && !closed.canFill && !closed.canClose);
});
test('no deadline stays open and extending deadline restores availability', () => {
  assert.ok(surveyState({status:'published',deadline:null},Date.parse(deadline)+1).canFill);
  assert.ok(surveyState({status:'published',deadline},Date.parse(deadline)-1).canFill);
  assert.ok(!surveyState({status:'published',deadline},Date.parse(deadline)).canFill);
});
test('deadline editor round trips local time without losing the instant', () => {
  assert.equal(localDateTime(null),'');
  assert.equal(new Date(localDateTime(deadline)).toISOString(),new Date(deadline).toISOString());
});
