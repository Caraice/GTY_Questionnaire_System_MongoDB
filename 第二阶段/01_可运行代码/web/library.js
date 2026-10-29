import {previewText, fillSelect} from './library-state.mjs';
const $ = id => document.getElementById(id);
let banks = [], busy = false, page = 1, bankPage = 1;
async function request(path, options = {}) {
  const token = sessionStorage.getItem('survey_token');
  if (!token) { $('login-link').hidden = false; throw new Error('请先登录。'); }
  const response = await fetch(path, {...options, headers: {'Content-Type': 'application/json', Authorization:`Bearer ${token}`}});
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) $('login-link').hidden = false;
    throw new Error(typeof data.detail === 'string' ? data.detail : '输入不合法，请检查名称。');
  }
  return data;
}
async function run(action) {
  if (busy) return;
  busy = true; $('library-fields').disabled = true; $('library-message').textContent = '';
  try { await action(); } catch (error) { $('library-message').textContent = error.message; }
  finally { busy = false; $('library-fields').disabled = !sessionStorage.getItem('survey_token'); }
}
async function load() {
  const selected = $('bank-filter').value;
  const bankResult = await request('/api/question-banks?page='+bankPage+'&page_size=20'); banks=bankResult.items;
  let nav=$('bank-pages');if(!nav){nav=document.createElement('p');nav.id='bank-pages';$('bank-filter').parentElement.after(nav);}nav.replaceChildren(document.createTextNode(`题库第 ${bankPage} 页 `));
  for(const [label,target,disabled] of [['上一页题库',bankPage-1,bankPage===1],['下一页题库',bankPage+1,!bankResult.has_more]]) {const b=document.createElement('button');b.type='button';b.textContent=label;b.disabled=disabled;b.addEventListener('click',()=>run(async()=>{bankPage=target;page=1;await load();}));nav.append(b);}
  fillSelect($('bank-filter'), banks, b => b.id, b => b.name, '全部题目（含未归类）');
  const shared = document.createElement('option'); shared.value = '__shared__'; shared.textContent = '共享给我的题目'; $('bank-filter').append(shared);
  $('bank-filter').value = selected;
  await loadQuestions();
}
async function loadQuestions() {
  const bank = $('bank-filter').value;
  const result = await request((bank === '__shared__' ? '/api/library/shared' : '/api/library/questions') + `?page=${page}&page_size=20` + (bank && bank !== '__shared__' ? '&bank_id='+encodeURIComponent(bank) : ''));
  const items = result.items;
  $('library-questions').replaceChildren();
  if (!items.length) $('library-questions').textContent = '还没有题目。新建题目后，可在“全部题目”里将它加入题库。';
  for (const item of items) {
    const card = document.createElement('article'); card.className = 'question-card';
    const title = document.createElement('h3'); title.textContent = item.latest?.content.title || '版本内容缺失';
    const select = document.createElement('select'); select.setAttribute('aria-label', `${title.textContent}的预览版本`);
    const preview = document.createElement('pre'); preview.className = 'library-preview';
    const versions = item.latest ? [item.latest] : []; // Other versions are paged on the history screen.
    fillSelect(select, versions, v => v.id, v => `版本 ${v.number}`);
    select.value = item.latest?.id || versions[0]?.id || '';
    const show = () => preview.textContent = previewText(versions.find(v => v.id === select.value));
    select.addEventListener('change', show); show(); card.append(title, select, preview);
    const detail = document.createElement('a'); detail.href = `/library/${item.id}`; detail.textContent = '版本历史、共享与引用'; card.append(detail);
    const statistics = document.createElement('a'); statistics.href = `/library/${item.id}/statistics`; statistics.textContent = '查看题目统计';
    const statisticsRow = document.createElement('p'); statisticsRow.append(statistics); card.append(statisticsRow);
    if (item.can_edit === false) {
      const p = document.createElement('p'); p.className = 'muted'; p.textContent = '获共享的版本可以预览和导入。修改时，请进入详情另存到自己的题库。'; card.append(p);
      $('library-questions').append(card); continue;
    }
    const hint = document.createElement('p'); hint.className = 'muted'; hint.textContent = '所属题库（可多选，勾选立即保存）'; card.append(hint);
    if (!banks.length) { const p = document.createElement('p'); p.textContent = '请先创建题库。题目已保留在全部题目中。'; card.append(p); }
    for (const bank of banks) {
      const label = document.createElement('label'); label.className = 'check-label';
      const check = document.createElement('input'); check.type = 'checkbox'; check.checked = item.bank_ids.includes(bank.id);
      label.append(check, document.createTextNode(bank.name)); card.append(label);
      check.addEventListener('change', () => {
        const wanted = check.checked;
        run(async () => {
          try {
            await request(`/api/question-banks/${bank.id}/questions/${item.id}`, {method:wanted ? 'PUT' : 'DELETE'});
            await loadQuestions(); $('library-message').textContent = wanted ? '已加入题库。' : '已移出题库，题目及版本仍保留。';
          } catch (error) { check.checked = !wanted; throw error; }
        });
      });
    }
    $('library-questions').append(card);
  }
  const nav=document.createElement('p'); nav.textContent=`第 ${page} 页 `;
  for(const [label,target,disabled] of [['上一页',page-1,page===1],['下一页',page+1,!result.has_more]]) { const b=document.createElement('button'); b.textContent=label;b.disabled=disabled;b.type='button';b.addEventListener('click',()=>run(async()=>{page=target;await loadQuestions();}));nav.append(b); }
  $('library-questions').append(nav);
}
$('bank-form').addEventListener('submit', event => {
  event.preventDefault();
  run(async () => {
    const bank = await request('/api/question-banks', {method:'POST', body:JSON.stringify({name:$('bank-name').value})});
    $('bank-name').value = ''; await load(); $('library-message').textContent = `题库“${bank.name}”已创建。`;
  });
});
$('bank-filter').addEventListener('change', () => {page=1;run(loadQuestions);});
$('library-refresh').addEventListener('click', () => run(load));
run(load);
