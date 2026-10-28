import {$,element,getJson,showError} from './view-utils.mjs';

const id = location.pathname.split('/').pop(), base = `/api/surveys/${encodeURIComponent(id)}`;
let loading = false, initialized = false;
function renderQuestion(question) {
  const section = element('section');
  section.append(element('h2',question.title),element('p',`本题回答数：${question.answered_count}`));
  if (question.options) {
    for (const option of question.options) {
      const row = element('div','','stat-option');
      const count = element('span',`${option.count} ${question.type === 'single' ? '人' : '次'}`);
      row.append(element('span',option.label),count); section.append(row);
    }
  }
  if (question.type === 'multiple') section.append(element('p',`选项被选择的总次数：${question.selection_count}`,'muted'));
  if (question.type === 'number') section.append(element('p',`平均值：${question.average == null ? '暂无有效回答' : question.average}`,'stat-total'));
  if (question.values) {
    if (!question.values.length) section.append(element('p','暂无回答','muted'));
    else {
      const list = element('ol','','answer-values');
      for (const value of question.values) list.append(element('li',String(value)));
      section.append(list);
    }
  }
  return section;
}
async function refresh() {
  if (loading) return;
  loading = true; $('statistics-refresh').disabled = true; $('question-filter').disabled = true;
  try {
    if (!initialized) {
      const survey = await getJson(base);
      $('page-title').textContent = survey.title; $('survey-description').textContent = survey.description;
      $('question-filter').replaceChildren(new Option('整份问卷',''));
      for (const question of survey.questions) $('question-filter').add(new Option(question.title,question.id));
      $('survey-fill-link').href = survey.url; initialized = true;
    }
    const question = $('question-filter').value;
    const data = await getJson(base+'/statistics'+(question ? '?question_id='+encodeURIComponent(question) : ''));
    $('statistics-total').textContent = `总提交数：${data.total_submissions}`;
    $('statistics-time').textContent = `本次更新时间：${new Date().toLocaleString()} · 每题回答数按实际有效答案计算`;
    $('statistics-results').replaceChildren(...data.questions.map(renderQuestion));
    $('statistics-controls').hidden = false; $('page-message').textContent = ''; $('page-login').hidden = true;
  } catch (error) {
    if ([401,404].includes(error.status)) { $('statistics-results').replaceChildren(); $('statistics-controls').hidden = true; }
    showError(error);
  } finally { loading = false; $('statistics-refresh').disabled = false; $('question-filter').disabled = false; }
}
$('statistics-refresh').addEventListener('click',refresh);
$('question-filter').addEventListener('change',refresh);
setInterval(() => { if (!document.hidden) refresh(); },10000);
window.addEventListener('focus',refresh);
refresh();
