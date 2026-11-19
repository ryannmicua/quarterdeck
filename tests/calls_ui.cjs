const assert = require('node:assert/strict');
const {script, targets} = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
class Element {
  constructor() { this.dataset = {}; this.listeners = {}; this.value = ''; this.style = {}; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  replaceChildren(...children) { this.children = children; }
  append() {}
  focus() { this.focused = true; }
}
const field = new Element(), words = new Element(), status = new Element(), opts = new Element();
words.querySelector = () => field;
const card = new Element(), next = new Element(), summary = new Element();
next.matches = selector => selector === "details.call";
next.querySelector = () => summary;
const queue = {
  querySelectorAll: () => card.parentElement === queue ? [card, next] : [next],
  querySelector: () => card.parentElement === queue ? card : next
};
card.dataset = {target: JSON.stringify(targets[0]), askKind: 'topic', askItem: 'maple-harbor-catalog', askPrefix: 'About decision: '};
card.querySelector = selector => ({'.call-status': status, '.call-opts': opts, '[data-call-words]': words,
  '[data-answer], textarea': field, '.call-line': {textContent: 'Merge PR 42 now?'}}[selector]);
card.classList = {add() {}};
card.remove = () => { card.removed = true; card.parentElement = null; };
card.parentElement = queue;
card.nextElementSibling = next;
global.document = {
  querySelectorAll: selector => selector === 'details[data-call]' ? [card] : [],
  querySelector: () => null, getElementById: id => id === "queue" ? queue : null, createElement: () => new Element(), title: 'Quarterdeck'
};
global.crypto = require('node:crypto').webcrypto;
let reducedMotion = true;
global.matchMedia = () => ({matches: reducedMotion});
let timeout;
global.setTimeout = fn => { timeout = fn; return 1; };
global.clearTimeout = () => { timeout = null; };
global.setInterval = () => 1;
global.clearInterval = () => {};
let fail = true;
const requests = [], delivered = new Map();
global.fetch = async (url, options) => {
  const body = JSON.parse(options.body);
  requests.push(body);
  delivered.set(body.id, body);
  if (fail) throw new Error('Connection lost after delivery');
  return {ok: true, json: async () => ({ok: true})};
};
require('node:vm').runInThisContext(script);
const submit = mode => words.listeners.submit({preventDefault() {}, submitter: {value: mode}});
const flush = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  const original = 'Keep all of these carefully typed words.';
  field.value = original;
  await submit('answer');
  status.children[1].listeners.click();
  assert.equal(timeout, null);
  assert.equal(requests.length, 0, 'Undo must send nothing');
  assert.equal(field.value, original, 'Undo must preserve the draft');
  await submit('answer');
  await timeout();
  assert.equal(field.value, original, 'Failed delivery must preserve the draft');
  assert.equal(opts.hidden, false, 'A failed send must allow retry');
  fail = false;
  await submit('answer');
  await timeout();
  assert.equal(requests.length, 2);
  assert.equal(requests[0].id, requests[1].id, 'Unchanged retries reuse the id after ambiguous delivery');
  assert.equal(delivered.size, 1, 'The idempotent receiver must see one answer');
  assert.deepEqual([requests[0].kind, requests[0].item_id, requests[0].text],
    ['decision', 'maple-harbor-catalog/merge-42', original], 'Answer only the selected decision, never its similar wait');
  assert.equal(card.removed, true, 'A successful answer leaves the queue');
  assert.equal(next.open, true, 'Reduced motion must still advance to the next call');
  assert.equal(summary.focused, true, 'Reduced motion must still focus the next call');
  card.removed = false;
  delete card.dataset.mutationPending;
  field.value = 'Why now?';
  fail = true;
  await submit('ask');
  assert.equal(field.value, 'Why now?', 'A failed question preserves the draft');
  fail = false;
  await submit('ask');
  assert.equal(requests[2].id, requests[3].id);
  assert.equal(field.value, '');
  assert.equal(card.removed, false, 'A question keeps the card');
  field.value = 'Why now?';
  await submit('ask');
  assert.notEqual(requests[3].id, requests[4].id, 'A new question after success gets a new id');
  card.listeners.click({target: {closest: () => ({dataset: {answer: 'Merge'}})}});
  await timeout();
  assert.equal(requests.at(-1).text, 'Merge', 'Option buttons still deliver their answer');
  delete card.dataset.mutationPending;
  card.dataset.target = JSON.stringify(targets[1]);
  require('node:vm').runInThisContext(script);
  card.listeners.click({target: {closest: () => ({dataset: {answer: 'Mark done'}})}});
  await timeout();
  assert.deepEqual([requests.at(-1).kind, requests.at(-1).item_id, requests.at(-1).text],
    ['topic', 'maple-harbor-catalog', 'Done: ["merge PR 42","Mark done"]'], 'Mark done only completes the selected wait');
  for (const reduced of [true, false]) {
    for (const inQueue of [true, false]) {
      reducedMotion = reduced;
      card.parentElement = inQueue ? queue : {};
      card.removed = false;
      delete card.dataset.mutationPending;
      next.open = false;
      summary.focused = false;
      card.listeners.click({target: {closest: () => ({dataset: {answer: 'Mark done'}})}});
      await timeout();
      assert.equal(next.open, inQueue, 'Only queue answers advance the next call, with either motion preference');
      assert.equal(summary.focused, inQueue, 'Only queue answers focus the next call, with either motion preference');
      if (!reduced) timeout();
      assert.equal(card.removed, true);
    }
  }
  await flush();
})().catch(error => { console.error(error); process.exitCode = 1; });
