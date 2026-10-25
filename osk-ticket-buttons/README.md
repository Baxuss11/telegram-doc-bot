# Кнопки билетов в группе «Билеты ОСК»

Модуль для `ticket_tracker.js`: кнопки исчезают после действия, повторов нет,
нажимать может любой участник группы.

## Как работает

**Сообщение «Новая оплата»** — кнопка `✅ Подтвердить A81`.

- Нажали, чек прошёл → кнопка исчезает, в сообщении появляется
  `✅ Подтвердил: Айбек · 25.10, 21:50`.
- Чек не прошёл → кнопка остаётся, ответом приходит `A81: ❌ <причина>`.
- Оплату подтвердили в другом месте (таблица, флажок) → когда трекер увидит готовый
  билет, кнопка у старого сообщения тоже исчезнет.

**Сообщение «Билет готов к отправке»** — кнопки `📲 Отправить билет в WhatsApp`
и `✅ Отправлено A81` (вместо прежней `SENT A81`).

- Нажали «Отправлено» → в таблицу пишется SENT, **обе кнопки исчезают**, сообщение
  больше не кликабельно, внизу появляется `✅ Отправлено в WhatsApp: Айбек · 25.10, 22:24`.
  Ссылка на билет в тексте остаётся.
- Повторные нажатия (пока кнопка ещё видна у другого менеджера) не пишут сообщений
  в группу — только всплывающая подсказка «уже отправлен (кто)».

**Кто может нажимать:** любой участник группы (создатель, админ, обычный участник).
Право проверяется через Telegram при нажатии, вышедший из группы теряет его в течение
5 минут. Чужие чаты и посторонние получают «Нет доступа».
Значит, **доступ = членство в группе**: держите группу закрытой, без публичной
ссылки-приглашения.

**Защита от повторов:**

- второе нажатие на ту же кнопку → «уже подтверждён / уже отмечен (кто)»;
- двое нажали одновременно → действие выполнится один раз;
- подтверждение идёт под тем же `flock`, что и cron-трекер, и не пересекается с его проходом.

## Состав

- `ticket_buttons.js` — клавиатуры, обработчик нажатий, хранилище, адаптер к командам трекера.
- `bot.js` — отдельный процесс, который принимает нажатия сразу (long polling).
- `test/` — 20 тестов: `node --test osk-ticket-buttons/test/*.test.js`

Зависимостей нет, нужен Node 18+.

## Подключение на сервере

Делать на сервере, где лежит
`/home/ductor/.ductor/workspace/projects/automations/osk-forum-ticket-tracker/`.

### 1. Узнать, кто сейчас принимает нажатия

Нажатия одного бота может получать **только один процесс** (getUpdates или webhook).
Сейчас нажатия получает **агент Ductor**: в его чате «БИЛЕТЫ» нажатие приходит как
«[ОТВЕТ ПОЛЬЗОВАТЕЛЯ] ✅ SENT A81», и агент отвечает «WhatsApp-кнопка выше остаётся».
Отсюда же повторные сообщения «Эта заявка уже обработана» на каждое нажатие и то, что
нажимать может только владелец (у Ductor свой список разрешённых пользователей).
Отключите обработку этих кнопок в Ductor (поиск по `callback_query`, `SENT`,
«Подтвердить» в его конфигурации и в трекере). Иначе `bot.js` будет получать
ошибку 409, или кнопки обработаются дважды.

Если тот же токен использует сам Ductor (`config.telegram_token`), заведите для
`@osk_forum_bilet_bot` отдельный токен в `OSK_FORUM_TRACKER_BOT_TOKEN`. Ductor остаётся
на своём боте.

### 2. Скопировать модуль

```
cp ticket_buttons.js bot.js /home/ductor/.ductor/workspace/projects/automations/osk-forum-ticket-tracker/
```

### 3. Правки в `ticket_tracker.js` (три места)

```js
const buttons = require('./ticket_buttons');
const buttonApi = buttons.createTelegramApi(BOT_TOKEN);             // тот же токен, что у трекера
const buttonStore = buttons.createJsonStore(path.join(__dirname, 'data', 'buttons_state.json'));
const buttonHandler = buttons.createButtonHandler({
  api: buttonApi, store: buttonStore, chatIds: [CHAT_ID],
  confirmRow: async () => ({ ok: false }), markSent: async () => ({ ok: false }) // в трекере не нужны
});
```

**а) Новая оплата** — там, где отправляется сообщение с кнопкой «Подтвердить»:

```js
const sent = await sendMessage(chatId, text, { reply_markup: buttons.confirmKeyboard(row) });
buttonHandler.rememberMessage(row, 'payment', sent);   // sent — объект message из ответа Telegram
```

**б) Билет готов** — там, где отправляется «Билет готов к отправке»:

```js
await buttonHandler.onTicketReady(row);                 // убирает «Подтвердить» у старого сообщения
const sent = await sendMessage(chatId, text, { reply_markup: buttons.ticketKeyboard(row, whatsappUrl) });
buttonHandler.rememberMessage(row, 'ticket', sent);
```

`row` — строка в формате `A81`. Старый формат кнопок замените этими функциями:
старые кнопки в уже отправленных сообщениях ответят «Эта кнопка устарела».

**в) Коды выхода.** `bot.js` вызывает `ticket_tracker.js --confirm-row A81` и
`--mark-sent-row A81`. Успехом считается код выхода 0. Проверьте, что при отказе
(чек не прошёл, нет строки) команда завершается с ненулевым кодом, а причину пишет
последней строкой в stderr — она уйдёт в группу ответом. Если сейчас при ошибке
код 0, добавьте `process.exitCode = 1` в эти ветки.

Если `--confirm-row` сам отправляет в группу «Чек не прошёл…», уберите это сообщение
из команды — иначе в группе будет два одинаковых ответа.

### 4. Запуск `bot.js` как службы

`/etc/systemd/system/osk-ticket-buttons.service`:

```ini
[Unit]
Description=OSK ticket buttons
After=network-online.target

[Service]
User=ductor
WorkingDirectory=/home/ductor/.ductor/workspace
EnvironmentFile=/home/ductor/.ductor/.env
Environment=OSK_TRACKER_SCRIPT=/home/ductor/.ductor/workspace/projects/automations/osk-forum-ticket-tracker/ticket_tracker.js
Environment=OSK_TRACKER_CWD=/home/ductor/.ductor/workspace
ExecStart=/usr/bin/node /home/ductor/.ductor/workspace/projects/automations/osk-forum-ticket-tracker/bot.js
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

В `.env` добавить (значения не публиковать):

```
OSK_FORUM_TRACKER_BOT_TOKEN=...      # если ещё нет
OSK_BUTTONS_CHAT_IDS=...             # id группы «Билеты ОСК» = OSK_FORUM_TRACKER_CHAT_ID
```

`OSK_TRACKER_CWD` должен совпадать с рабочей папкой в `run_tracker.sh` — трекер ищет
`data/` и `.env` относительно неё.

```
sudo systemctl daemon-reload
sudo systemctl enable --now osk-ticket-buttons
journalctl -u osk-ticket-buttons -f
```

Если systemd нет — запустить в tmux под тем же пользователем.

### 5. Проверка на тестовой строке

1. Тестовая заявка (с пометкой test) → сообщение с «Подтвердить».
2. Нажать с телефона менеджера (не владельца) → кнопка исчезла, строка «Подтвердил».
3. Нажать ещё раз в другом клиенте → «уже подтверждён».
4. Пришло «Билет готов» → «✅ Отправлено» → обе кнопки исчезли, в таблице SENT.
5. Вторая тестовая заявка: подтвердить в таблице → у её сообщения кнопка «Подтвердить»
   исчезает после следующего прохода трекера.

## Что хранится

`data/buttons_state.json` (права 600): номер последнего обновления Telegram, id сообщений
и кто/когда нажал. Имён, телефонов и текста сообщений там нет.
