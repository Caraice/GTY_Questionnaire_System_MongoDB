export const $ = id => document.getElementById(id);
export function element(tag, text = '', className = '') {
  const node = document.createElement(tag); node.textContent = text; node.className = className; return node;
}
export async function getJson(path) {
  const token = sessionStorage.getItem('survey_token');
  const response = await fetch(path,{headers:token ? {Authorization:`Bearer ${token}`} : {},cache:'no-store',signal:AbortSignal.timeout(10000)});
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(typeof data.detail === 'string' ? data.detail : '请求失败，请稍后重试');
    error.status = response.status; throw error;
  }
  return data;
}
export function showError(error) {
  $('page-message').textContent = error.message || '连接失败，请稍后重试';
  $('page-login').hidden = error.status !== 401;
  $('page-login').href = '/?return='+encodeURIComponent(location.pathname);
}
export function displayAnswer(question) {
  const answer = question.answer;
  if (answer == null || (Array.isArray(answer) && !answer.length)) return '未填写';
  const labels = new Map((question.options || []).map(o => [o.id,o.label]));
  if (question.type === 'single') return labels.get(answer) ?? answer;
  if (question.type === 'multiple') return answer.map(id => labels.get(id) ?? id).join('、');
  return String(answer);
}
