import test from 'node:test';
import assert from 'node:assert/strict';
import {deadlineInput,parseDeadline} from '../../01_可运行代码/web/deadline-input.mjs';
import {displayAnswer} from '../../01_可运行代码/web/view-utils.mjs';

test('deadline uses dd format and preserves local instant and empty value',() => {
  assert.equal(deadlineInput(null),''); assert.equal(parseDeadline('   '),null);
  const instant = '2030-12-31T16:05:09.000Z';
  assert.match(deadlineInput(instant),/^\d{4}\/\d{2}\/\d{2} \d{2}:\d{2}:\d{2}$/);
  assert.equal(parseDeadline(deadlineInput(instant)),instant);
  assert.equal(new Date(parseDeadline('2028/02/29 00:00')).getDate(),29);
});
test('deadline rejects invalid calendar days, ranges and ambiguous date formats',() => {
  for (const value of ['2027/02/29 00:00','2028/04/31 00:00','2028/00/01 00:00','2028/01/00 00:00','2028/01/01 24:00','2028/01/01 00:60','2028-01-01T00:00','tomorrow']) {
    assert.throws(() => parseDeadline(value));
  }
});
test('saved answers render labels and preserve zero and optional omissions',() => {
  const options = [{id:'a',label:'苹果'},{id:'b',label:'香蕉'}];
  assert.equal(displayAnswer({type:'single',answer:'a',options}),'苹果');
  assert.equal(displayAnswer({type:'multiple',answer:['a','b'],options}),'苹果、香蕉');
  assert.equal(displayAnswer({type:'number',answer:0}),'0');
  assert.equal(displayAnswer({type:'text',answer:'<script>alert(1)</script>'}),'<script>alert(1)</script>');
  assert.equal(displayAnswer({type:'text',answer:null}),'未填写');
});
