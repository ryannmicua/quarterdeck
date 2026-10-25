const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const page = JSON.parse(fs.readFileSync(0, 'utf8'));

class Element {
  constructor(tag = 'div') {
    this.tagName = tag;
    this.children = [];
    this.dataset = {};
    this.events = {};
    this.value = '';
    this.checked = false;
    this.disabled = false;
  }
  append(...items) {
    for (const item of items) { item.parent = this; this.children.push(item); }
  }
  replaceChildren() { this.children = []; }
  insertBefore(item, other) {
    item.parent.children.splice(item.parent.children.indexOf(item), 1);
    this.children.splice(this.children.indexOf(other), 0, item);
  }
  get previousElementSibling() { return this.parent.children[this.parent.children.indexOf(this) - 1]; }
  get nextElementSibling() { return this.parent.children[this.parent.children.indexOf(this) + 1]; }
  querySelector(tag) { return this.children.find(child => child.tagName === tag); }
  matches(selector) {
    const id = selector.match(/^#([\w-]+)/);
    if (!id || id[1] !== this.id) return false;
    if (selector.includes(':disabled') && !this.disabled) return false;
    return [...selector.matchAll(/\[data-([\w-]+)=([^\]]+)\]/g)].every(([, name, value]) =>
      this.dataset[name.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] === value);
  }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, callback) { (this.events[name] ??= []).push(callback); }
  async emit(name) {
    for (const callback of this.events[name] ?? []) await callback({target: this});
    if (this.parent) await this.parent.emit(name);
  }
}

async function main() {
  const ids = Object.fromEntries(['views-data', 'view-sections', 'view-name', 'view-startup',
    'view-status', 'view-save', 'view-editor'].map(id => [id, new Element()]));
  for (const [id, element] of Object.entries(ids)) element.id = id;
  ids['views-data'].textContent = page.data;
  for (const id of ['view-name', 'view-sections', 'view-startup']) ids['view-editor'].append(ids[id]);
  const data = JSON.parse(page.data);
  const buttons = data.views.map(entry => Object.assign(new Element(), {dataset: {viewButton: entry.name}}));
  const panels = data.views.map(entry => Object.assign(new Element(), {dataset: {viewPanel: entry.name}}));
  const editor = ids['view-editor'], save = ids['view-save'], list = ids['view-sections'];
  let reloads = 0, retries = 0, request, complete;
  const context = vm.createContext({
    document: {
      getElementById: id => ids[id], createElement: tag => new Element(tag),
      addEventListener() {},
      querySelectorAll: selector => selector === '[data-view-button]' ? buttons : selector === '[data-view-panel]' ? panels : [],
      querySelector: selector => Object.values(ids).find(element => selector.split(',').some(part => element.matches(part.trim()))),
    },
    sessionStorage: {getItem: () => null, setItem() {}},
    location: {reload: () => reloads++},
    setTimeout: () => retries++,
    fetch: async (_url, options) => {
      request = JSON.parse(options.body);
      return new Promise(resolve => { complete = resolve; });
    },
  });
  const viewScript = page.scripts.find(script => script.includes('const data = JSON.parse(document.getElementById("views-data")'));
  const reloadScript = page.scripts.find(script => script.includes('function quarterdeckReload()'));
  vm.runInContext(viewScript, context);
  vm.runInContext(reloadScript, context);
  const refresh = () => vm.runInContext('quarterdeckReload()', context);
  const section = row => JSON.parse(row.dataset.section);
  assert.deepEqual(list.children.filter(row => section(row).type === 'project').map(row => section(row).project),
    ['reef-demo', 'harbor-app']);
  assert(list.children.some(row => section(row).type === 'done'));
  refresh();
  assert.equal(reloads, 1);
  for (const [id, event] of [['view-name', 'input'], ['view-startup', 'change']]) {
    editor.dataset.dirty = 'false';
    await ids[id].emit(event);
    refresh();
    assert.equal(reloads, 1);
  }
  editor.dataset.dirty = 'false';
  const project = list.children.find(row => section(row).project === 'reef-demo');
  project.children[0].checked = true;
  await project.children[0].emit('change');
  refresh();
  assert.equal(reloads, 1);
  editor.dataset.dirty = 'false';
  await project.children[2].emit('click');
  refresh();
  assert.equal(reloads, 1);
  assert(retries >= 4);
  ids['view-name'].value = 'Only reef';
  for (const row of list.children) row.children[0].checked = row === project;
  const pending = save.emit('click');
  assert.equal(save.dataset.saving, 'true');
  editor.dataset.dirty = 'false';
  refresh();
  assert.equal(reloads, 1);
  assert.deepEqual(request.sections, [{type: 'project', project: 'reef-demo'}]);
  complete({ok: false, text: async () => 'Unavailable'});
  await pending;
  assert.equal(save.disabled, false);
  assert.equal(save.dataset.saving, 'false');
  await ids['view-name'].emit('input');
  const retry = save.emit('click');
  complete({ok: true});
  await retry;
  assert.equal(editor.dataset.dirty, 'false');
  assert.equal(reloads, 2);
  await buttons.find(button => button.dataset.viewButton === 'Priorities').emit('click');
  assert.deepEqual(list.children.filter(row => row.children[0].checked).map(section),
    [{type: 'project', project: 'reef-demo'}, {type: 'project', project: 'harbor-app'}, {type: 'waiting'}]);
}
main().catch(error => { process.stderr.write(String(error.stack) + '\n'); process.exitCode = 1; });
