// Run: node --experimental-vm-modules --test prototype/tests/submitted-input.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
import { webcrypto } from 'node:crypto';

const appUrl = new URL('../app.js', import.meta.url);
const input = JSON.parse(await readFile(new URL('../data/agent3-input.json', import.meta.url), 'utf8'));
const response = JSON.parse(await readFile(new URL('../data/agent3-preview.json', import.meta.url), 'utf8'));
const appSource = await readFile(appUrl, 'utf8');
const fixtureSource = await readFile(new URL('../fixtures.js', import.meta.url), 'utf8');
const storageKey = 'weave-prototype-agent3-session-v1';
const changedInput = text => { const result = structuredClone(input); result.constraints[0].text = text; return result; };
class ApiError extends Error { constructor(status, message) { super(message); this.status = status; } }

// Execute the actual app handlers with only browser I/O and the API transport replaced.
// Exporting internal handlers here keeps production code free of test hooks.
async function boot(options = {}) {
  const store = options.store || new Map();
  const callbacks = options.callbacks || {};
  class FakeApi {
    prepare(body) { return callbacks.prepare ? callbacks.prepare(body) : structuredClone(response); }
    get(id) { return callbacks.get ? callbacks.get(id) : structuredClone(response); }
    review(id, body) { return callbacks.review(id, body); }
  }
  const node = { innerHTML: '', addEventListener() {}, querySelector() { return null; }, textContent: '', hidden: true };
  const context = vm.createContext({
    structuredClone, URL, console, setTimeout, clearTimeout, crypto: webcrypto,
    document: { activeElement: null, querySelector() { return node; } },
    location: { hash: '' }, window: { addEventListener() {}, scrollTo() {} },
    sessionStorage: { getItem: key => store.get(key) || null, setItem: (key, value) => store.set(key, value), removeItem: key => store.delete(key) },
    fetch: async url => ({ ok: true, json: async () => structuredClone(String(url).includes('agent3-input') ? input : response) }),
  });
  const transport = new vm.SyntheticModule(['Agent3Api', 'ApiError', 'MockPreview'], function () {
    this.setExport('Agent3Api', FakeApi); this.setExport('ApiError', ApiError);
    this.setExport('MockPreview', class { constructor() {} });
  }, { context });
  const fixtures = new vm.SourceTextModule(fixtureSource, { context });
  const module = new vm.SourceTextModule(`${appSource}\nexport { state, prepare, restore, acceptView, inputChanged, submittedConditions, localBar, review, canApprove }; export const cleanup = () => clearTimeout(toastTimer);`, {
    context, identifier: appUrl.href, initializeImportMeta(meta) { meta.url = appUrl.href; },
  });
  await module.link(specifier => specifier === './api.js' ? transport : fixtures);
  await module.evaluate();
  return { ...module.namespace, store };
}

function saved(store) { return JSON.parse(store.get(storageKey)); }

test('successful prepare keeps its submitted conditions when editor changes during await', async t => {
  let resolve, received;
  const pending = new Promise(done => { resolve = done; });
  const app = await boot({ callbacks: { prepare: body => { received = body; return pending; } } });
  t.after(app.cleanup);
  const sent = changedInput('제출한 오픈일 2026-12-01 유지');
  app.state.inputText = JSON.stringify(sent);
  const work = app.prepare();
  app.state.inputText = JSON.stringify(changedInput('미제출 오픈일 2027-01-20'));
  resolve(structuredClone(response)); await work;
  assert.equal(received.constraints[0].text, sent.constraints[0].text);
  assert.equal(app.state.submittedInput.constraints[0].text, sent.constraints[0].text);
  assert.equal(app.state.signature, JSON.stringify(sent));
  assert.equal(app.inputChanged(), true);
  assert.match(app.submittedConditions(), /2026-12-01/);
  assert.doesNotMatch(app.submittedConditions(), /2027-01-20/);
  assert.equal(saved(app.store).submittedInput.constraints[0].text, sent.constraints[0].text);
  assert.match(saved(app.store).inputText, /2027-01-20/);
});

test('failed new prepare retains successful snapshot and invalidation after reload', async t => {
  const app = await boot({ callbacks: { prepare: () => { throw new ApiError(502, 'offline failure'); } } });
  t.after(app.cleanup);
  const sent = changedInput('성공 조건 2026-12-03');
  app.acceptView(structuredClone(response), sent);
  app.state.inputText = JSON.stringify(changedInput('실패한 새 조건 2027-02-01'));
  await app.prepare();
  assert.match(app.submittedConditions(), /2026-12-03/);
  assert.doesNotMatch(app.submittedConditions(), /2027-02-01/);
  assert.equal(app.state.error.status, 502);
  const restored = await boot({ store: app.store }); t.after(restored.cleanup);
  assert.match(restored.submittedConditions(), /2026-12-03/);
  assert.equal(restored.state.invalidated, true);
  assert.equal(restored.inputChanged(), true);
  assert.equal(restored.canApprove(), false);
});

test('legacy signature restores submitted conditions instead of unsaved editor text', async t => {
  const legacy = changedInput('기존 세션 제출 조건 2026-12-05');
  const store = new Map([[storageKey, JSON.stringify({ sessionId: response.session_id, signature: JSON.stringify(legacy), inputText: JSON.stringify(changedInput('미제출 조건 2027-03-01')) })]]);
  const app = await boot({ store }); t.after(app.cleanup);
  assert.match(app.submittedConditions(), /2026-12-05/);
  assert.doesNotMatch(app.submittedConditions(), /2027-03-01/);
  assert.equal(app.inputChanged(), true);
});

test('missing legacy snapshot shows unknown, and multiple/empty conditions stay truthful', async t => {
  const store = new Map([[storageKey, JSON.stringify({ sessionId: response.session_id, inputText: JSON.stringify(input) })]]);
  const app = await boot({ store }); t.after(app.cleanup);
  assert.match(app.submittedConditions(), /제출 조건 기록이 없습니다/);
  const sent = changedInput('<script>untrusted</script>');
  sent.constraints.push({ ...sent.constraints[0], text: '두 번째 조건' });
  app.acceptView(structuredClone(response), sent);
  assert.match(app.submittedConditions(), /&lt;script&gt;/);
  assert.match(app.submittedConditions(), /두 번째 조건/);
  app.acceptView(structuredClone(response), { ...sent, constraints: [] });
  assert.match(app.submittedConditions(), /제출된 조건 없음/);
});

test('PM approval and reload retain submitted snapshot and response model label', async t => {
  const sent = changedInput('초안과 일치할 조건 2026-12-10');
  const view = structuredClone(response); view.impact.meta.model = 'fake:snapshot-regression';
  const app = await boot({ callbacks: { review: (id, body) => ({ ...structuredClone(view), phase: 'DRAFT_READY', approval: { approved: body.approved }, draft: { impact: `조건: ${sent.constraints[0].text}` } }) } });
  t.after(app.cleanup);
  app.state.inputText = JSON.stringify(sent); app.acceptView(view, sent);
  app.state.ack = true; await app.review(true);
  assert.equal(app.state.view.phase, 'DRAFT_READY');
  assert.match(app.state.view.draft.impact, /2026-12-10/);
  assert.match(app.submittedConditions(), /2026-12-10/);
  assert.match(app.localBar(), /API 응답 fake:snapshot-regression/);
  const restored = await boot({ store: app.store, callbacks: { get: () => structuredClone(app.state.view) } }); t.after(restored.cleanup);
  assert.match(restored.submittedConditions(), /2026-12-10/);
  assert.equal(restored.inputChanged(), false);
});
