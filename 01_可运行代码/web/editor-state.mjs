// These helpers never derive identity from array position or editable labels.
export const QUESTION_TYPES = {single: '单选题', multiple: '多选题', text: '文本填空', number: '数字填空'};
const newId = (prefix) => `${prefix}_${crypto.randomUUID()}`;
export const isChoice = (type) => type === 'single' || type === 'multiple';
export const createOption = () => ({id: newId('o'), label: ''});

export function defaultValidation(type) {
  if (type === 'multiple') return {min_choices: null, max_choices: null, exact_choices: null};
  if (type === 'text') return {min_length: 0, max_length: null};
  if (type === 'number') return {minimum: null, maximum: null, integer_only: false};
  return {};
}

export function createQuestion(type) {
  return {id: newId('q'), type, title: '', required: false, validation: defaultValidation(type),
    ...(isChoice(type) ? {options: [createOption(), createOption()]} : {})};
}

export function changeType(question, type) {
  if (question.type === type) return question;
  // Keep options while switching types locally, including a switch back before save.
  const choiceOptions = question.options || question.choiceOptions;
  return {id: question.id, title: question.title, required: question.required, type,
    validation: defaultValidation(type),
    ...(isChoice(type) ? {options: choiceOptions || [createOption(), createOption()]} : {choiceOptions})};
}

export function moveItem(items, id, offset) {
  const from = items.findIndex(item => item.id === id);
  const to = from + offset;
  if (from < 0 || to < 0 || to >= items.length) return items;
  const result = [...items];
  const [item] = result.splice(from, 1);
  result.splice(to, 0, item);
  return result;
}

export function questionPayload(question) {
  const result = {id: question.id, type: question.type, title: question.title,
    required: question.required, validation: question.validation};
  if (isChoice(question.type)) result.options = question.options.map(({id, label}) => ({id, label}));
  return result;
}
