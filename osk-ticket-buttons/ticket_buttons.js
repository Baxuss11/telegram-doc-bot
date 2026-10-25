'use strict';

/**
 * Кнопки Telegram для группы «Билеты ОСК».
 *
 * Жизненный цикл сообщений:
 *   новая оплата   → [✅ Подтвердить A81]
 *     нажали и чек прошёл (или подтвердили в другом месте) → кнопка исчезает,
 *     в сообщении строка «✅ Подтвердил: …»
 *   билет готов    → [📲 Отправить билет в WhatsApp] [✅ Отправлено A81]
 *     нажали «Отправлено» → обе кнопки исчезают, сообщение больше не кликабельно,
 *     строка «✅ Отправлено в WhatsApp: …»
 *
 * Нажимать может любой участник разрешённой группы. Повторное или двойное
 * нажатие ничего не повторяет. Зависимостей нет, нужен Node 18+.
 */

const fs = require('node:fs');
const path = require('node:path');
const { execFile } = require('node:child_process');

const ROW_PATTERN = /^A\d{1,5}$/;
const CALLBACK_PATTERN = /^osk:(c|s):(A\d{1,5})$/;
const MEMBER_STATUSES = new Set(['creator', 'administrator', 'member']);
const MAX_ERROR_LENGTH = 300;
const LOCK_BUSY_EXIT = 75;

// ---------------------------------------------------------------------------
// Клавиатуры — использовать в ticket_tracker.js при отправке сообщений.

function confirmKeyboard(row) {
  assertRow(row);
  return { inline_keyboard: [[{ text: `✅ Подтвердить ${row}`, callback_data: `osk:c:${row}` }]] };
}

function ticketKeyboard(row, whatsappUrl) {
  assertRow(row);
  const keyboard = [];
  if (whatsappUrl) keyboard.push([{ text: '📲 Отправить билет в WhatsApp', url: whatsappUrl }]);
  keyboard.push([{ text: `✅ Отправлено ${row}`, callback_data: `osk:s:${row}` }]);
  return { inline_keyboard: keyboard };
}

function parseCallbackData(data) {
  const m = CALLBACK_PATTERN.exec(String(data || ''));
  if (!m) return null;
  return { action: m[1] === 'c' ? 'confirm' : 'sent', row: m[2] };
}

function assertRow(row) {
  if (!ROW_PATTERN.test(String(row))) throw new Error(`Неверная строка: ${row}`);
}

// ---------------------------------------------------------------------------
// Telegram Bot API

function createTelegramApi(token, { fetchImpl = fetch, timeoutMs = 60000 } = {}) {
  if (!token) throw new Error('Нет токена бота');
  return {
    async call(method, params = {}) {
      const res = await fetchImpl(`https://api.telegram.org/bot${token}/${method}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(params),
        signal: AbortSignal.timeout(timeoutMs)
      });
      const body = await res.json();
      if (!body.ok) {
        const err = new Error(`${method}: ${body.description || res.status}`);
        err.code = body.error_code;
        throw err;
      }
      return body.result;
    }
  };
}

// ---------------------------------------------------------------------------
// Состояние: только id сообщений и отметки, без имён и телефонов.
// Файл перечитывается при каждой операции: его пишут и трекер, и bot.js.

function createJsonStore(file) {
  const load = () => {
    try {
      return Object.assign({ offset: 0, rows: {} }, JSON.parse(fs.readFileSync(file, 'utf8')));
    } catch (err) {
      if (err.code === 'ENOENT') return { offset: 0, rows: {} };
      throw err;
    }
  };
  const save = (data) => {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const tmp = `${file}.${process.pid}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(data, null, 2), { mode: 0o600 });
    fs.renameSync(tmp, file);
  };
  return {
    get: (row) => load().rows[row] || {},
    patch(row, fields) {
      const data = load();
      data.rows[row] = Object.assign({}, data.rows[row], fields);
      save(data);
    },
    getOffset: () => load().offset || 0,
    setOffset(offset) {
      const data = load();
      data.offset = offset;
      save(data);
    }
  };
}

// ---------------------------------------------------------------------------
// Обработчик нажатий

function createButtonHandler({
  api,
  store,
  confirmRow,
  markSent,
  chatIds,
  adminIds = [],
  timeZone = 'Asia/Bishkek',
  memberCacheMs = 5 * 60 * 1000,
  now = () => new Date(),
  log = console
}) {
  const allowedChats = new Set((chatIds || []).map(String));
  const admins = new Set((adminIds || []).map(String));
  const busy = new Set();
  const memberCache = new Map();
  const timeFormat = new Intl.DateTimeFormat('ru-RU', {
    timeZone, day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
  });

  const stamp = () => timeFormat.format(now());
  const who = (user) => {
    const name = [user.first_name, user.last_name].filter(Boolean).join(' ').trim();
    return name || (user.username ? `@${user.username}` : `id ${user.id}`);
  };

  async function answer(cq, text, alert = false) {
    try {
      await api.call('answerCallbackQuery', { callback_query_id: cq.id, text, show_alert: alert });
    } catch (err) {
      log.warn(`answerCallbackQuery: ${err.message}`);
    }
  }

  async function isAuthorized(chatId, user) {
    if (admins.has(String(user.id))) return true;
    if (!allowedChats.has(String(chatId))) return false;
    const key = `${chatId}:${user.id}`;
    const cached = memberCache.get(key);
    if (cached && now().getTime() - cached.at < memberCacheMs) return cached.ok;
    let ok = false;
    try {
      const member = await api.call('getChatMember', { chat_id: chatId, user_id: user.id });
      ok = MEMBER_STATUSES.has(member.status) || (member.status === 'restricted' && member.is_member === true);
    } catch (err) {
      log.warn(`getChatMember: ${err.message}`);
    }
    memberCache.set(key, { ok, at: now().getTime() });
    return ok;
  }

  /** Дописывает строку статуса и заменяет кнопки. Форматирование и ссылки сохраняются. */
  async function finishMessage(message, line, keyboard = []) {
    const base = { chat_id: message.chat.id, message_id: message.message_id, reply_markup: { inline_keyboard: keyboard } };
    try {
      if (typeof message.text === 'string') {
        await api.call('editMessageText', Object.assign({}, base, {
          text: `${message.text}\n\n${line}`,
          entities: message.entities,
          link_preview_options: { is_disabled: true }
        }));
      } else if (typeof message.caption === 'string') {
        await api.call('editMessageCaption', Object.assign({}, base, {
          caption: `${message.caption}\n\n${line}`,
          caption_entities: message.caption_entities
        }));
      } else {
        await api.call('editMessageReplyMarkup', base);
      }
    } catch (err) {
      if (/message is not modified/i.test(err.message)) return;
      log.warn(`editMessage: ${err.message}`);
      await removeButtons(message.chat.id, message.message_id);
    }
  }

  async function removeButtons(chatId, messageId) {
    try {
      await api.call('editMessageReplyMarkup', {
        chat_id: chatId, message_id: messageId, reply_markup: { inline_keyboard: [] }
      });
    } catch (err) {
      if (!/message is not modified/i.test(err.message)) log.warn(`editMessageReplyMarkup: ${err.message}`);
    }
  }

  async function reportError(message, row, text) {
    const clean = String(text || 'Неизвестная ошибка').replace(/\s+/g, ' ').trim()
      .replace(new RegExp(`^${row}:\\s*`), '').slice(0, MAX_ERROR_LENGTH);
    try {
      await api.call('sendMessage', {
        chat_id: message.chat.id,
        text: `${row}: ❌ ${clean}`,
        reply_parameters: { message_id: message.message_id, allow_sending_without_reply: true }
      });
    } catch (err) {
      log.warn(`sendMessage: ${err.message}`);
    }
  }

  async function runStep(fn, row, user) {
    try {
      const result = await fn(row, user);
      return result && typeof result === 'object' ? result : { ok: Boolean(result) };
    } catch (err) {
      return { ok: false, message: err.message };
    }
  }

  async function handleConfirm(cq, row) {
    const record = store.get(row);
    if (record.confirmedAt) {
      await removeButtons(cq.message.chat.id, cq.message.message_id);
      return answer(cq, `${row} уже подтверждён (${record.confirmedBy}).`);
    }
    await answer(cq, `⏳ ${row}: проверяю чек и выдаю билеты…`);
    const result = await runStep(confirmRow, row, cq.from);
    if (!result.ok) return reportError(cq.message, row, result.message || 'Оплата не подтверждена.');

    const by = who(cq.from);
    store.patch(row, {
      confirmedAt: now().toISOString(),
      confirmedBy: by,
      payment: { chat_id: cq.message.chat.id, message_id: cq.message.message_id }
    });
    await finishMessage(cq.message, `✅ Подтвердил: ${by} · ${stamp()}`);
  }

  async function handleSent(cq, row) {
    const record = store.get(row);
    if (record.sentAt) {
      await removeButtons(cq.message.chat.id, cq.message.message_id);
      return answer(cq, `${row} уже отправлен (${record.sentBy}).`);
    }
    const result = await runStep(markSent, row, cq.from);
    if (!result.ok) {
      await answer(cq, `${row}: не удалось отметить`, false);
      return reportError(cq.message, row, result.message || 'Не удалось отметить отправку.');
    }
    const by = who(cq.from);
    store.patch(row, {
      sentAt: now().toISOString(),
      sentBy: by,
      ticket: { chat_id: cq.message.chat.id, message_id: cq.message.message_id }
    });
    await answer(cq, `✅ ${row}: отмечено «Отправлено»`);
    await finishMessage(cq.message, `✅ Отправлено в WhatsApp: ${by} · ${stamp()}`);
  }

  async function handleUpdate(update) {
    const cq = update && update.callback_query;
    if (!cq) return;
    const parsed = parseCallbackData(cq.data);
    if (!parsed || !cq.message) {
      return answer(cq, 'Эта кнопка устарела. Дождитесь нового сообщения.', true);
    }
    if (!(await isAuthorized(cq.message.chat.id, cq.from))) {
      log.warn(`Отказано: ${cq.from.id} в чате ${cq.message.chat.id}, ${cq.data}`);
      return answer(cq, 'Нет доступа. Кнопки работают только для участников группы билетов.', true);
    }
    const key = `${parsed.action}:${parsed.row}`;
    if (busy.has(key)) return answer(cq, `${parsed.row}: уже выполняется, подождите.`);
    busy.add(key);
    try {
      if (parsed.action === 'confirm') await handleConfirm(cq, parsed.row);
      else await handleSent(cq, parsed.row);
    } finally {
      busy.delete(key);
    }
  }

  /** Вызывать из трекера сразу после отправки сообщения «Новая оплата» или «Билет готов». */
  function rememberMessage(row, kind, sentMessage) {
    assertRow(row);
    if (kind !== 'payment' && kind !== 'ticket') throw new Error(`Неизвестный тип: ${kind}`);
    store.patch(row, { [kind]: { chat_id: sentMessage.chat.id, message_id: sentMessage.message_id } });
  }

  /**
   * Вызывать из трекера, когда строка стала «билет готов» — в том числе если оплату
   * подтвердили не кнопкой, а в таблице. Убирает кнопку «Подтвердить» у старого сообщения.
   */
  async function onTicketReady(row) {
    assertRow(row);
    const record = store.get(row);
    if (record.payment && !record.paymentClosed) {
      await removeButtons(record.payment.chat_id, record.payment.message_id);
    }
    store.patch(row, {
      paymentClosed: true,
      confirmedAt: record.confirmedAt || now().toISOString(),
      confirmedBy: record.confirmedBy || 'таблица'
    });
  }

  return { handleUpdate, rememberMessage, onTicketReady, isAuthorized };
}

// ---------------------------------------------------------------------------
// Получение нажатий

/** Один проход getUpdates. Offset сохраняется ДО обработки: сбой не повторит подтверждение. */
async function pollOnce(api, handler, store, { timeout = 0, log = console } = {}) {
  const updates = await api.call('getUpdates', {
    offset: store.getOffset(),
    timeout,
    allowed_updates: ['callback_query']
  });
  for (const update of updates) {
    store.setOffset(update.update_id + 1);
    try {
      await handler.handleUpdate(update);
    } catch (err) {
      log.error(`update ${update.update_id}: ${err.stack || err.message}`);
    }
  }
  return updates.length;
}

// ---------------------------------------------------------------------------
// Адаптер к существующим командам трекера

/**
 * Запускает `ticket_tracker.js --confirm-row A81` / `--mark-sent-row A81` под тем же
 * flock, что и cron (run_tracker.sh), чтобы подтверждение не шло параллельно с проходом трекера.
 * Успех = код выхода 0.
 */
function trackerCliAdapter({
  trackerScript,
  cwd = path.dirname(trackerScript),
  nodeBin = process.execPath,
  lockFile = '/tmp/osk-forum-ticket-tracker.lock',
  lockWaitSec = 150,
  timeoutMs = 240000,
  env = process.env
}) {
  const run = (args) => new Promise((resolve) => {
    execFile('flock', ['-w', String(lockWaitSec), '-E', String(LOCK_BUSY_EXIT), lockFile, nodeBin, trackerScript, ...args],
      { cwd, env, timeout: timeoutMs, maxBuffer: 4 * 1024 * 1024 },
      (err, stdout, stderr) => {
        if (!err) return resolve({ ok: true });
        const lastLine = (text) => String(text || '').split('\n').map((l) => l.trim()).filter(Boolean).pop();
        let message = lastLine(stderr) || lastLine(stdout) || err.message;
        if (err.killed) message = 'Трекер не ответил вовремя. Проверьте строку в таблице.';
        else if (err.code === LOCK_BUSY_EXIT) message = 'Трекер занят, нажмите ещё раз через минуту.';
        resolve({ ok: false, message });
      });
  });
  return {
    confirmRow: (row) => run(['--confirm-row', row]),
    markSent: (row) => run(['--mark-sent-row', row])
  };
}

module.exports = {
  confirmKeyboard,
  ticketKeyboard,
  parseCallbackData,
  createTelegramApi,
  createJsonStore,
  createButtonHandler,
  pollOnce,
  trackerCliAdapter
};
