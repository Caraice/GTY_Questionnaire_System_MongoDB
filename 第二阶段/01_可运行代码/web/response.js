import {$,element,getJson,showError,displayAnswer} from './view-utils.mjs';

async function load() {
  try {
    const data = await getJson('/api/me/responses/'+encodeURIComponent(location.pathname.split('/').pop()));
    $('page-title').textContent = data.title;
    $('response-meta').textContent = `提交时间：${new Date(data.submitted_at).toLocaleString()} · ${data.anonymous ? '匿名提交' : '实名提交'}`;
    for (const question of data.questions) {
      const section = element('section'); section.append(element('h2',question.title),element('p',displayAnswer(question),'saved-answer'));
      $('response-answers').append(section);
    }
  } catch (error) { $('page-title').textContent = '暂时无法查看这份答卷'; showError(error); }
}
load();
