// Запуск: node --test osk-ticket-buttons/test/*.test.js
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {
  confirmKeyboard, ticketKeyboard, parseCallbackData, createJsonStore, createButtonHandler,
  pollOnce, trackerCliAdapter
} = require('../ticket_buttons');

const GROUP = -1001234567890;
const MANAGER = { id: 111, first_name: 'Айбек' };
const OWNER = { id: 222, first_name: 'Бактияр', last_name: 'К.' };
const STRANGER = { id: 999, first_name: 'Чужой' };
const silent = { warn() {}, error() {}, log() {} };

function tmpFile() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'osk-buttons-'));
  return path.join(dir, 'state.json');
}

function fakeApi({ members = { 111: 'member', 222: 'creator' }, failEdit = null } = {}) {
  const calls = [];
  return {
    calls,
    of: (method) => calls.filter((c) => c.method === method).map((c) => c.params),
    async call(method, params) {
      calls.push({ method, params });
      if (method === 'getChatMember') return { status: members[params.user_id] || 'left' };
      if (failEdit && method === failEdit.method) {
        const err = new Error(failEdit.message);
        throw err;
      }
      return true;
    }
  };
}

function paymentMessage(row = 'A81') {
  return {
    message_id: 501,
    chat: { id: GROUP },
    text: `Новая оплата\nСумма — 3 000 сом.\nСтрока заявки: ${row}`,
    entities: [{ type: 'bold', offset: 0, length: 12 }],
    reply_markup: confirmKeyboard(row)
  };
}

function ticketMessage(row = 'A81') {
  return {
    message_id: 777,
    chat: { id: GROUP },
    text: `✅ Билет готов к отправке\nБилет: https://site/ticket?x\nСтрока заявки: ${row}`,
    entities: [{ type: 'url', offset: 32, length: 22 }],
    reply_markup: ticketKeyboard(row, 'https://wa.me/996555?text=x')
  };
}

let nextId = 1;
function press(handler, from, message, data) {
  return handler.handleUpdate({ update_id: nextId, callback_query: { id: String(nextId++), from, message, data } });
}

function setup(overrides = {}) {
  const api = fakeApi(overrides.api);
  const store = createJsonStore(tmpFile());
  const confirmed = [];
  const sent = [];
  const handler = createButtonHandler(Object.assign({
    api,
    store,
    chatIds: [GROUP],
    log: silent,
    now: () => new Date('2026-10-25T15:50:00Z'),
    confirmRow: async (row) => { confirmed.push(row); return { ok: true }; },
    markSent: async (row) => { sent.push(row); return { ok: true }; }
  }, overrides.handler));
  return { api, store, handler, confirmed, sent };
}

test('клавиатуры и разбор callback_data', () => {
  assert.deepEqual(confirmKeyboard('A81').inline_keyboard[0][0], { text: '✅ Подтвердить A81', callback_data: 'osk:c:A81' });
  const kb = ticketKeyboard('A81', 'https://wa.me/1').inline_keyboard;
  assert.equal(kb[0][0].url, 'https://wa.me/1');
  assert.equal(kb[1][0].callback_data, 'osk:s:A81');
  assert.deepEqual(parseCallbackData('osk:c:A81'), { action: 'confirm', row: 'A81' });
  assert.deepEqual(parseCallbackData('osk:s:A1205'), { action: 'sent', row: 'A1205' });
  assert.equal(parseCallbackData('osk:c:A81;rm -rf'), null);
  assert.equal(parseCallbackData('confirm A81'), null);
  assert.throws(() => confirmKeyboard('81'));
});

test('«Подтвердить» менеджером группы: кнопка исчезает, текст и ссылки сохраняются', async () => {
  const { api, handler, store, confirmed } = setup();
  await press(handler, MANAGER, paymentMessage(), 'osk:c:A81');

  assert.deepEqual(confirmed, ['A81']);
  assert.match(api.of('answerCallbackQuery')[0].text, /проверяю чек/);
  const edit = api.of('editMessageText')[0];
  assert.deepEqual(edit.reply_markup, { inline_keyboard: [] });
  assert.ok(edit.text.startsWith('Новая оплата\nСумма — 3 000 сом.'));
  assert.ok(edit.text.endsWith('✅ Подтвердил: Айбек · 25.10, 21:50'));
  assert.deepEqual(edit.entities, [{ type: 'bold', offset: 0, length: 12 }]);
  assert.equal(store.get('A81').confirmedBy, 'Айбек');
});

test('чек не прошёл: кнопка остаётся, ошибка ответом на сообщение', async () => {
  const { api, handler, store } = setup({
    handler: { confirmRow: async () => ({ ok: false, message: 'Чек не прошёл проверку суммы.' }) }
  });
  await press(handler, MANAGER, paymentMessage(), 'osk:c:A81');

  assert.equal(api.of('editMessageText').length, 0);
  assert.equal(api.of('editMessageReplyMarkup').length, 0);
  const msg = api.of('sendMessage')[0];
  assert.equal(msg.text, 'A81: ❌ Чек не прошёл проверку суммы.');
  assert.equal(msg.reply_parameters.message_id, 501);
  assert.equal(store.get('A81').confirmedAt, undefined);
});

test('исключение в подтверждении не роняет обработчик и не блокирует повтор', async () => {
  let calls = 0;
  const { api, handler } = setup({
    handler: { confirmRow: async () => { calls += 1; if (calls === 1) throw new Error('Sheets 503'); return { ok: true }; } }
  });
  await press(handler, MANAGER, paymentMessage(), 'osk:c:A81');
  assert.match(api.of('sendMessage')[0].text, /Sheets 503/);
  await press(handler, MANAGER, paymentMessage(), 'osk:c:A81');
  assert.equal(calls, 2);
  assert.equal(api.of('editMessageText').length, 1);
});

test('повторное нажатие после подтверждения ничего не повторяет', async () => {
  const { api, handler, confirmed } = setup();
  await press(handler, MANAGER, paymentMessage(), 'osk:c:A81');
  await press(handler, OWNER, paymentMessage(), 'osk:c:A81');
  assert.deepEqual(confirmed, ['A81']);
  assert.match(api.of('answerCallbackQuery')[1].text, /уже подтверждён \(Айбек\)/);
  assert.deepEqual(api.of('editMessageReplyMarkup')[0].reply_markup, { inline_keyboard: [] });
});

test('два менеджера жмут одновременно — подтверждение выполняется один раз', async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  const confirmed = [];
  const { api, handler } = setup({
    handler: { confirmRow: async (row) => { confirmed.push(row); await gate; return { ok: true }; } }
  });
  const first = press(handler, MANAGER, paymentMessage(), 'osk:c:A81');
  await new Promise((r) => setImmediate(r));
  await press(handler, OWNER, paymentMessage(), 'osk:c:A81');
  release();
  await first;
  assert.deepEqual(confirmed, ['A81']);
  assert.ok(api.of('answerCallbackQuery').some((a) => /уже выполняется/.test(a.text)));
});

test('«Отправлено»: исчезают обе кнопки, повтор не вызывает отметку', async () => {
  const { api, handler, sent, store } = setup();
  await press(handler, MANAGER, ticketMessage(), 'osk:s:A81');
  assert.deepEqual(sent, ['A81']);
  const edit = api.of('editMessageText')[0];
  assert.deepEqual(edit.reply_markup, { inline_keyboard: [] });
  assert.ok(edit.text.endsWith('📤 Отправлено: Айбек · 25.10, 21:50'));
  assert.deepEqual(edit.entities, ticketMessage().entities);
  assert.equal(store.get('A81').sentBy, 'Айбек');

  await press(handler, OWNER, ticketMessage(), 'osk:s:A81');
  assert.deepEqual(sent, ['A81']);
  assert.match(api.of('answerCallbackQuery').pop().text, /уже отмечен/);
});

test('ошибка отметки «Отправлено»: кнопки остаются', async () => {
  const { api, handler, store } = setup({ handler: { markSent: async () => ({ ok: false, message: 'Нет номера билета' }) } });
  await press(handler, MANAGER, ticketMessage(), 'osk:s:A81');
  assert.equal(api.of('editMessageText').length, 0);
  assert.match(api.of('sendMessage')[0].text, /A81: ❌ Нет номера билета/);
  assert.equal(store.get('A81').sentAt, undefined);
});

test('чужая группа и вышедший из группы — отказ без действий', async () => {
  const { api, handler, confirmed } = setup();
  const otherChat = Object.assign(paymentMessage(), { chat: { id: -100555 } });
  await press(handler, MANAGER, otherChat, 'osk:c:A81');
  await press(handler, STRANGER, paymentMessage(), 'osk:c:A81');
  assert.deepEqual(confirmed, []);
  const answers = api.of('answerCallbackQuery');
  assert.equal(answers.length, 2);
  answers.forEach((a) => { assert.match(a.text, /Нет доступа/); assert.equal(a.show_alert, true); });
});

test('admin id работает и вне группы (например, в личке с ботом)', async () => {
  const { handler, confirmed } = setup({ handler: { adminIds: [222] } });
  const privateChat = Object.assign(paymentMessage(), { chat: { id: 222 } });
  await press(handler, OWNER, privateChat, 'osk:c:A81');
  assert.deepEqual(confirmed, ['A81']);
});

test('старая кнопка с другим форматом — понятный ответ, без действий', async () => {
  const { api, handler, confirmed } = setup();
  await press(handler, MANAGER, paymentMessage(), 'confirm_A81');
  assert.deepEqual(confirmed, []);
  assert.match(api.of('answerCallbackQuery')[0].text, /устарела/);
});

test('подтвердили в таблице: onTicketReady убирает кнопку у старого сообщения один раз', async () => {
  const { api, handler, store } = setup();
  handler.rememberMessage('A81', 'payment', paymentMessage());
  await handler.onTicketReady('A81');
  await handler.onTicketReady('A81');
  const edits = api.of('editMessageReplyMarkup');
  assert.equal(edits.length, 1);
  assert.deepEqual(edits[0], { chat_id: GROUP, message_id: 501, reply_markup: { inline_keyboard: [] } });
  assert.equal(store.get('A81').confirmedBy, 'таблица');

  // После этого нажатие на старую кнопку не запускает подтверждение.
  const confirmed = [];
  const h2 = createButtonHandler({ api, store, chatIds: [GROUP], log: silent,
    confirmRow: async (r) => { confirmed.push(r); return { ok: true }; }, markSent: async () => ({ ok: true }) });
  await h2.handleUpdate({ update_id: 1, callback_query: { id: 'x', from: MANAGER, message: paymentMessage(), data: 'osk:c:A81' } });
  assert.deepEqual(confirmed, []);
});

test('сообщение-фото с подписью редактируется через editMessageCaption', async () => {
  const { api, handler } = setup();
  const photo = { message_id: 9, chat: { id: GROUP }, caption: 'Чек A81', caption_entities: [] };
  await press(handler, MANAGER, photo, 'osk:c:A81');
  const edit = api.of('editMessageCaption')[0];
  assert.ok(edit.caption.startsWith('Чек A81\n\n✅ Подтвердил'));
});

test('если текст изменить нельзя — хотя бы убираем кнопки', async () => {
  const { api, handler } = setup({ api: { failEdit: { method: 'editMessageText', message: 'Bad Request: message is too long' } } });
  await press(handler, MANAGER, paymentMessage(), 'osk:c:A81');
  assert.deepEqual(api.of('editMessageReplyMarkup')[0].reply_markup, { inline_keyboard: [] });
});

test('состояние переживает перезапуск и не хранит текст сообщений', async () => {
  const file = tmpFile();
  const api = fakeApi();
  const h = createButtonHandler({ api, store: createJsonStore(file), chatIds: [GROUP], log: silent,
    confirmRow: async () => ({ ok: true }), markSent: async () => ({ ok: true }) });
  await press(h, MANAGER, paymentMessage(), 'osk:c:A81');
  const raw = fs.readFileSync(file, 'utf8');
  assert.ok(!raw.includes('Сумма'));
  assert.equal(createJsonStore(file).get('A81').confirmedBy, 'Айбек');
  assert.equal((fs.statSync(file).mode & 0o777).toString(8), '600');
});

test('pollOnce: offset сохраняется до обработки, ошибка одного нажатия не мешает следующему', async () => {
  const store = createJsonStore(tmpFile());
  const seen = [];
  const api = { async call(method, params) {
    assert.equal(method, 'getUpdates');
    assert.deepEqual(params.allowed_updates, ['callback_query']);
    return [{ update_id: 10 }, { update_id: 11 }];
  } };
  const handler = { async handleUpdate(u) {
    seen.push([u.update_id, store.getOffset()]);
    if (u.update_id === 10) throw new Error('boom');
  } };
  const n = await pollOnce(api, handler, store, { log: silent });
  assert.equal(n, 2);
  assert.deepEqual(seen, [[10, 11], [11, 12]]);
});

test('адаптер CLI: код выхода 0 — успех, иначе последняя строка вывода', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'osk-tracker-'));
  const script = path.join(dir, 'ticket_tracker.js');
  fs.writeFileSync(script, `
    const [flag, row] = process.argv.slice(2);
    if (row === 'A1') process.exit(0);
    console.log('Читаю чек...');
    console.error(row + ': Чек не прошёл проверку суммы.');
    process.exit(3);
  `);
  const adapter = trackerCliAdapter({ trackerScript: script, lockFile: path.join(dir, 'lock') });
  assert.deepEqual(await adapter.confirmRow('A1'), { ok: true });
  const bad = await adapter.markSent('A2');
  assert.equal(bad.ok, false);
  assert.equal(bad.message, 'A2: Чек не прошёл проверку суммы.');

  fs.writeFileSync(script, `console.log('Билет не выдан: нет ссылки на чек'); process.exit(3);`);
  const bad2 = await adapter.confirmRow('A2');
  assert.equal(bad2.message, 'Билет не выдан: нет ссылки на чек');
});

test('ошибка с префиксом строки не дублирует «A81: A81:»', async () => {
  const { api, handler } = setup({ handler: { confirmRow: async () => ({ ok: false, message: 'A81: Чек не прошёл.' }) } });
  await press(handler, MANAGER, paymentMessage(), 'osk:c:A81');
  assert.equal(api.of('sendMessage')[0].text, 'A81: ❌ Чек не прошёл.');
});

test('адаптер CLI: занятая блокировка даёт понятное сообщение', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'osk-tracker-'));
  const script = path.join(dir, 'ticket_tracker.js');
  fs.writeFileSync(script, 'process.exit(0);');
  const lock = path.join(dir, 'lock');
  const { spawn } = require('node:child_process');
  const holder = spawn('flock', [lock, 'sleep', '3']);
  await new Promise((r) => setTimeout(r, 200));
  const adapter = trackerCliAdapter({ trackerScript: script, lockFile: lock, lockWaitSec: 1 });
  const res = await adapter.confirmRow('A5');
  holder.kill();
  assert.deepEqual(res, { ok: false, message: 'Трекер занят, нажмите ещё раз через минуту.' });
});

test('два процесса с одним файлом состояния не затирают записи друг друга', () => {
  const file = tmpFile();
  const tracker = createJsonStore(file);
  const bot = createJsonStore(file);
  bot.getOffset();
  tracker.patch('A81', { payment: { chat_id: GROUP, message_id: 501 } });
  bot.patch('A81', { confirmedBy: 'Айбек' });
  bot.setOffset(42);
  tracker.patch('A82', { payment: { chat_id: GROUP, message_id: 502 } });
  const fresh = createJsonStore(file);
  assert.deepEqual(fresh.get('A81'), { payment: { chat_id: GROUP, message_id: 501 }, confirmedBy: 'Айбек' });
  assert.equal(fresh.get('A82').payment.message_id, 502);
  assert.equal(fresh.getOffset(), 42);
});
