#!/usr/bin/env node
'use strict';

/**
 * Отдельный процесс, который слушает нажатия кнопок в группе «Билеты ОСК».
 * Реагирует сразу, а не раз в 2 минуты вместе с cron.
 *
 * Переменные окружения:
 *   OSK_BUTTONS_BOT_TOKEN   токен бота (иначе OSK_FORUM_TRACKER_BOT_TOKEN)
 *   OSK_BUTTONS_CHAT_IDS    id группы(групп) через запятую, например -1001234567890
 *   OSK_BUTTONS_ADMIN_IDS   необязательно: user id, которым можно и вне группы
 *   OSK_TRACKER_SCRIPT      полный путь к ticket_tracker.js
 *   OSK_TRACKER_CWD         рабочая папка, как в run_tracker.sh (по умолчанию папка скрипта)
 *   OSK_BUTTONS_STATE       файл состояния (по умолчанию data/buttons_state.json рядом с трекером)
 */

const path = require('node:path');
const {
  createTelegramApi, createJsonStore, createButtonHandler, pollOnce, trackerCliAdapter
} = require('./ticket_buttons');

function required(name, value) {
  if (!value) {
    console.error(`Не задана переменная ${name}`);
    process.exit(2);
  }
  return value;
}

const token = required('OSK_BUTTONS_BOT_TOKEN', process.env.OSK_BUTTONS_BOT_TOKEN || process.env.OSK_FORUM_TRACKER_BOT_TOKEN);
const chatIds = required('OSK_BUTTONS_CHAT_IDS', process.env.OSK_BUTTONS_CHAT_IDS).split(',').map((s) => s.trim()).filter(Boolean);
const adminIds = (process.env.OSK_BUTTONS_ADMIN_IDS || '').split(',').map((s) => s.trim()).filter(Boolean);
const trackerScript = required('OSK_TRACKER_SCRIPT', process.env.OSK_TRACKER_SCRIPT);
const stateFile = process.env.OSK_BUTTONS_STATE || path.join(path.dirname(trackerScript), 'data', 'buttons_state.json');

const api = createTelegramApi(token);
const store = createJsonStore(stateFile);
const adapter = trackerCliAdapter({ trackerScript, cwd: process.env.OSK_TRACKER_CWD || undefined });
const handler = createButtonHandler({
  api, store, chatIds, adminIds, confirmRow: adapter.confirmRow, markSent: adapter.markSent
});

let stopping = false;
process.on('SIGTERM', () => { stopping = true; });
process.on('SIGINT', () => { stopping = true; });

(async function main() {
  console.log(`Кнопки OSK запущены. Группы: ${chatIds.length}, состояние: ${stateFile}`);
  let backoff = 1000;
  while (!stopping) {
    try {
      await pollOnce(api, handler, store, { timeout: 30 });
      backoff = 1000;
    } catch (err) {
      if (err.code === 409) {
        console.error('409: этот токен уже слушает другой процесс (getUpdates или webhook). ' +
          'Нажатия должен принимать только один процесс — см. README.');
      } else {
        console.error(`getUpdates: ${err.message}`);
      }
      await new Promise((r) => setTimeout(r, backoff));
      backoff = Math.min(backoff * 2, 60000);
    }
  }
  console.log('Остановлено.');
})();
