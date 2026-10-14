// Запуск: node --test osk-ticket-issuer/test
// Загружает TicketIssuer.gs в песочницу с имитацией SpreadsheetApp и прогоняет сценарии выдачи.
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(path.join(__dirname, '..', 'TicketIssuer.gs'), 'utf8');

const HEADERS = [
  'Номер заявки', 'Дата и время', 'Фамилия', 'Имя', 'Телефон', 'Сумма', 'Ссылка на чек',
  'Действие менеджера', 'Источник', 'Язык', 'Статус оплаты', 'Номер билета', 'Ссылка на билет',
  'Ссылка билета для копирования', 'WhatsApp с билетом', 'ID оплаты', 'Кол-во билетов',
  '№ билета в оплате', 'Статус входа', 'Время входа', 'Кто отметил', 'QR входа'
];

class FakeSheet {
  constructor(name, rows) {
    this.name = name;
    this.cells = rows.map((r) => r.map((v) => cellOf(v)));
    this.maxRows = 1000;
    this.maxCols = 26;
    this.validations = {};
  }
  getName() { return this.name; }
  getLastRow() {
    for (let r = this.cells.length; r > 0; r--) {
      if ((this.cells[r - 1] || []).some((c) => c && (c.v !== '' || c.f))) return r;
    }
    return 0;
  }
  getLastColumn() {
    let max = 0;
    this.cells.forEach((row) => row.forEach((c, i) => { if (c && (c.v !== '' || c.f)) max = Math.max(max, i + 1); }));
    return max;
  }
  getMaxRows() { return this.maxRows; }
  getMaxColumns() { return this.maxCols; }
  insertRowsAfter(_after, n) { this.maxRows += n; }
  insertColumnAfter() { this.maxCols += 1; }
  cell(r, c) {
    while (this.cells.length < r) this.cells.push([]);
    const row = this.cells[r - 1];
    while (row.length < c) row.push(cellOf(''));
    return row[c - 1];
  }
  getRange(row, col, numRows = 1, numCols = 1) { return new FakeRange(this, row, col, numRows, numCols); }
  col(title) { return HEADERS_OF(this).indexOf(title) + 1; }
  get(row, title) { return this.cell(row, this.col(title)); }
}

function HEADERS_OF(sheet) { return sheet.cells[0].map((c) => String(c.v)); }

function cellOf(v) {
  if (v && typeof v === 'object' && 'f' in v) return { v: '', f: v.f };
  return { v, f: '' };
}

function display(c) {
  if (c.f) {
    const m = /HYPERLINK\("[^"]*","([^"]*)"\)/.exec(c.f);
    return m ? m[1] : c.f;
  }
  if (c.v === true) return 'TRUE';
  if (c.v === false) return 'FALSE';
  return String(c.v);
}

class FakeRange {
  constructor(sheet, row, col, nr, nc) { Object.assign(this, { sheet, row, col, nr, nc }); }
  map(fn) {
    const out = [];
    for (let r = 0; r < this.nr; r++) {
      const line = [];
      for (let c = 0; c < this.nc; c++) line.push(fn(this.sheet.cell(this.row + r, this.col + c)));
      out.push(line);
    }
    return out;
  }
  getValues() { return this.map((c) => c.v); }
  getDisplayValues() { return this.map(display); }
  getFormulas() { return this.map((c) => c.f); }
  getRichTextValues() { return this.map(() => ({ getLinkUrl: () => null })); }
  getValue() { return this.sheet.cell(this.row, this.col).v; }
  setValue(v) { Object.assign(this.sheet.cell(this.row, this.col), { v, f: '' }); return this; }
  setFormula(f) { Object.assign(this.sheet.cell(this.row, this.col), { v: '', f }); return this; }
  setDataValidation(v) { this.sheet.validations[this.col] = { rows: this.nr, rule: v }; return this; }
  getRow() { return this.row; }
  getColumn() { return this.col; }
  getNumRows() { return this.nr; }
  getNumColumns() { return this.nc; }
  getSheet() { return this.sheet; }
}

function load(sheet, props = {}) {
  const alerts = [];
  const triggers = [];
  const ctx = {
    SpreadsheetApp: {
      getActive: () => ({ getSheetByName: (n) => (n === sheet.getName() ? sheet : null) }),
      getActiveSheet: () => sheet,
      getActiveRange: () => sheet.activeRange,
      getUi: () => ({
        alert: (m) => alerts.push(m),
        createMenu: () => { const m = { addItem: () => m, addSeparator: () => m, addToUi: () => m }; return m; }
      }),
      newDataValidation: () => ({ requireCheckbox() { return this; }, build: () => 'checkbox' }),
      flush: () => {}
    },
    LockService: { getDocumentLock: () => ({ tryLock: () => true, releaseLock: () => {} }) },
    PropertiesService: {
      getDocumentProperties: () => ({
        getProperty: (k) => (k in props ? props[k] : null),
        setProperty: (k, v) => { props[k] = v; }
      })
    },
    Utilities: { formatDate: () => '14.10 20:00' },
    Session: { getActiveUser: () => ({ getEmail: () => 'manager@example.com' }) },
    ScriptApp: {
      getProjectTriggers: () => triggers,
      deleteTrigger: (t) => triggers.splice(triggers.indexOf(t), 1),
      newTrigger: (fn) => {
        const b = { forSpreadsheet: () => b, onEdit: () => b, create: () => triggers.push({ getHandlerFunction: () => fn }) };
        return b;
      }
    }
  };
  vm.createContext(ctx);
  vm.runInContext(SOURCE, ctx);
  ctx.alerts = alerts;
  ctx.props = props;
  ctx.triggers = triggers;
  return ctx;
}

// Строка таблицы по заголовкам.
function row(fields) {
  return HEADERS.map((h) => (h in fields ? fields[h] : ''));
}

const RECEIPT = 'https://drive.google.com/file/d/receipt-1/view';

function baseSheet(extraRows = []) {
  return new FakeSheet('Билеты и оплаты', [
    HEADERS,
    row({ 'Номер заявки': 'OSK-Z-0001', 'Фамилия': 'Иванов', 'Имя': 'Иван', 'Телефон': '+996 555 111 222',
      'Сумма': 3000, 'Ссылка на чек': RECEIPT, 'Статус оплаты': 'Оплачено', 'Номер билета': 'OSK-0050',
      'Ссылка на билет': { f: '=HYPERLINK("https://x/ticket-demo.html?ticket=OSK-0050","OSK-0050")' },
      'WhatsApp с билетом': 'SENT', 'ID оплаты': 'pay_old' }),
    ...extraRows
  ]);
}

function setupSheet(extraRows) {
  const sheet = baseSheet(extraRows);
  const ctx = load(sheet);
  ctx.oskSetup();
  return { sheet, ctx };
}

function tick(ctx, sheet, rowNumber, title = '✅ Выдать билеты') {
  const col = sheet.col(title);
  sheet.getRange(rowNumber, col).setValue(true);
  ctx.oskOnEdit({ range: sheet.getRange(rowNumber, col), user: { getEmail: () => 'manager@example.com' } });
}

const single = row({ 'Номер заявки': 'OSK-Z-0002', 'Фамилия': 'Асанова', 'Имя': 'Айгерим',
  'Телефон': '0700 123 456', 'Сумма': '3 000', 'Ссылка на чек': RECEIPT.replace('1', '2'),
  'Статус оплаты': 'Новая', 'ID оплаты': 'pay_single', 'Кол-во билетов': 1, '№ билета в оплате': 1 });

function guest(i, extra = {}) {
  return row(Object.assign({ 'Номер заявки': 'OSK-Z-001' + i, 'Фамилия': 'Гость' + i, 'Имя': 'Имя' + i,
    'Телефон': '+996555000111', 'Сумма': 3000, 'Ссылка на чек': RECEIPT.replace('1', 'g'),
    'Статус оплаты': 'Новая', 'ID оплаты': 'pay_group', 'Кол-во билетов': 3, '№ билета в оплате': i }, extra));
}

test('настройка добавляет 3 колонки, флажки и один триггер; повтор безопасен', () => {
  const { sheet, ctx } = setupSheet();
  const headers = HEADERS_OF(sheet);
  assert.deepEqual(headers.slice(22), ['✅ Выдать билеты', '📤 Отправлено', 'Результат выдачи']);
  assert.equal(sheet.validations[sheet.col('✅ Выдать билеты')].rule, 'checkbox');
  assert.equal(ctx.props.OSK_LAST_TICKET_NUMBER, '50');
  ctx.oskSetup();
  assert.equal(HEADERS_OF(sheet).length, 25);
  assert.equal(ctx.triggers.length, 1);
});

test('одиночный билет: номер, статус, ссылки, WhatsApp на номер 996', () => {
  const { sheet, ctx } = setupSheet([single]);
  tick(ctx, sheet, 3);
  assert.equal(sheet.get(3, 'Номер билета').v, 'OSK-0051');
  assert.equal(sheet.get(3, 'Статус оплаты').v, 'Оплачено');
  assert.equal(sheet.get(3, 'Действие менеджера').v, 'Подтвердить');
  assert.equal(sheet.get(3, 'Ссылка билета для копирования').v,
    'https://osk-forum-2026.baktiiar.chatgpt.site/ticket-demo.html?ticket=OSK-0051&name=' +
    encodeURIComponent('Айгерим Асанова'));
  assert.match(sheet.get(3, 'Ссылка на билет').f, /^=HYPERLINK\("https:\/\/osk-forum.*","OSK-0051"\)$/);
  assert.match(sheet.get(3, 'WhatsApp с билетом').f, /^=HYPERLINK\("https:\/\/wa\.me\/996700123456\?text=/);
  assert.match(sheet.get(3, 'Результат выдачи').v, /^✅ .*OSK-0051 · Чек 3 000 сом · manager@example\.com$/);
  assert.equal(ctx.props.OSK_LAST_TICKET_NUMBER, '51');
});

test('группа из 3: флажок у второго гостя выдаёт всем по порядку № в оплате', () => {
  // Строки специально перемешаны.
  const { sheet, ctx } = setupSheet([guest(2), guest(1), guest(3)]);
  tick(ctx, sheet, 3);
  assert.equal(sheet.get(4, 'Номер билета').v, 'OSK-0051'); // гость 1
  assert.equal(sheet.get(3, 'Номер билета').v, 'OSK-0052'); // гость 2
  assert.equal(sheet.get(5, 'Номер билета').v, 'OSK-0053'); // гость 3
  [3, 4, 5].forEach((r) => {
    assert.equal(sheet.get(r, '✅ Выдать билеты').v, true);
    assert.match(sheet.get(r, 'Результат выдачи').v, /Группа 3 бил\., чек 9 000 сом/);
  });
});

test('неполная группа: отказ, флажок снят, ничего не выдано', () => {
  const { sheet, ctx } = setupSheet([guest(1), guest(2)]);
  tick(ctx, sheet, 3);
  assert.equal(sheet.get(3, 'Номер билета').v, '');
  assert.equal(sheet.get(4, 'Номер билета').v, '');
  assert.equal(sheet.get(3, '✅ Выдать билеты').v, false);
  assert.match(sheet.get(3, 'Результат выдачи').v, /^❌ .*указано билетов: 3, а строк с этим чеком: 2/);
  assert.equal(ctx.props.OSK_LAST_TICKET_NUMBER, '50');
});

test('неверная сумма, пустое имя и короткий телефон перечисляются в отказе', () => {
  const bad = row({ 'Фамилия': 'Петров', 'Имя': '', 'Телефон': '+996 55', 'Сумма': 6000,
    'Ссылка на чек': 'https://drive/x', 'ID оплаты': 'pay_bad' });
  const { sheet, ctx } = setupSheet([bad]);
  tick(ctx, sheet, 3);
  const msg = sheet.get(3, 'Результат выдачи').v;
  assert.match(msg, /нет фамилии или имени/);
  assert.match(msg, /неполный телефон/);
  assert.match(msg, /сумма «6000», ожидалось 3000/);
  assert.equal(sheet.get(3, 'Номер билета').v, '');
});

test('отклонённая оплата не выдаётся', () => {
  const { sheet, ctx } = setupSheet([guest(1, { 'Кол-во билетов': 1, 'Статус оплаты': 'Отклонено' })]);
  tick(ctx, sheet, 3);
  assert.match(sheet.get(3, 'Результат выдачи').v, /статус «Отклонено»/);
});

test('повторный флажок не выдаёт новые номера', () => {
  const { sheet, ctx } = setupSheet([single]);
  tick(ctx, sheet, 3);
  tick(ctx, sheet, 3);
  assert.equal(sheet.get(3, 'Номер билета').v, 'OSK-0051');
  assert.equal(ctx.props.OSK_LAST_TICKET_NUMBER, '51');
  const r = ctx.oskPlanIssue_(ctx.oskReadModel_(sheet), 3, { now: '', actor: '' });
  assert.equal(r.writes.length, 0);
  assert.match(r.message, /уже выданы: OSK-0051/);
});

test('номер, проставленный заранее без оплаты, сохраняется и подтверждается', () => {
  const pre = row({ 'Фамилия': 'Садыков', 'Имя': 'Бакыт', 'Телефон': '+996777888999', 'Сумма': 3000,
    'Ссылка на чек': 'https://drive/pre', 'Номер билета': 'OSK-0010',
    'Ссылка на билет': { f: '=HYPERLINK("https://site/ticket-demo.html?ticket=OSK-0010&name=B","OSK-0010")' } });
  const { sheet, ctx } = setupSheet([pre]);
  tick(ctx, sheet, 3);
  assert.equal(sheet.get(3, 'Номер билета').v, 'OSK-0010');
  assert.equal(sheet.get(3, 'Статус оплаты').v, 'Оплачено');
  assert.match(sheet.get(3, 'Ссылка на билет').f, /ticket=OSK-0010&name=B/); // ссылку не трогали
  assert.match(sheet.get(3, 'WhatsApp с билетом').f,
    new RegExp(encodeURIComponent('https://site/ticket-demo.html?ticket=OSK-0010&name=B')));
  assert.equal(ctx.props.OSK_LAST_TICKET_NUMBER, '50'); // счётчик не сдвинулся
});

test('удалённая строка с последним номером не приводит к повтору номера', () => {
  const sheet = baseSheet([single]);
  const ctx = load(sheet, { OSK_LAST_TICKET_NUMBER: '120' });
  ctx.oskSetup();
  tick(ctx, sheet, 3);
  assert.equal(sheet.get(3, 'Номер билета').v, 'OSK-0121');
});

test('группа без ID оплаты собирается по одинаковой ссылке на чек из HYPERLINK', () => {
  const link = (u) => ({ f: '=HYPERLINK("' + u + '","Чек")' });
  const a = row({ 'Фамилия': 'Ким', 'Имя': 'Анна', 'Телефон': '+996555123123', 'Сумма': 3000,
    'Ссылка на чек': link('https://drive/a'), 'Кол-во билетов': 2 });
  const b = row({ 'Фамилия': 'Ким', 'Имя': 'Олег', 'Телефон': '+996555123123', 'Сумма': 3000,
    'Ссылка на чек': link('https://drive/a'), 'Кол-во билетов': 2 });
  const other = row({ 'Фамилия': 'Чужой', 'Имя': 'Чек', 'Телефон': '+996555999999', 'Сумма': 3000,
    'Ссылка на чек': link('https://drive/b') });
  const { sheet, ctx } = setupSheet([a, other, b]);
  tick(ctx, sheet, 5);
  assert.equal(sheet.get(3, 'Номер билета').v, 'OSK-0051');
  assert.equal(sheet.get(5, 'Номер билета').v, 'OSK-0052');
  assert.equal(sheet.get(4, 'Номер билета').v, ''); // одинаковая подпись «Чек», но другой файл
});

test('флажок «Отправлено» пишет SENT только для выданного билета', () => {
  const { sheet, ctx } = setupSheet([single, guest(1, { 'Кол-во билетов': 1, 'ID оплаты': 'pay_x' })]);
  tick(ctx, sheet, 4, '📤 Отправлено');
  assert.equal(sheet.get(4, '📤 Отправлено').v, false);
  assert.match(sheet.get(4, 'Результат выдачи').v, /билет ещё не выдан/);

  tick(ctx, sheet, 3);
  tick(ctx, sheet, 3, '📤 Отправлено');
  assert.equal(sheet.get(3, 'WhatsApp с билетом').v, 'SENT');
  assert.match(sheet.get(3, 'Результат выдачи').v, /OSK-0051 отмечен отправленным/);
});

test('правки в других колонках и на других листах игнорируются', () => {
  const { sheet, ctx } = setupSheet([single]);
  ctx.oskOnEdit({ range: sheet.getRange(3, sheet.col('Фамилия')) });
  const other = new FakeSheet('Другой лист', [['x']]);
  ctx.oskOnEdit({ range: other.getRange(2, 23) });
  assert.equal(sheet.get(3, 'Номер билета').v, '');
});

test('без настройки меню просит запустить «Настройку», а не падает', () => {
  const sheet = baseSheet([single]);
  const ctx = load(sheet);
  sheet.activeRange = sheet.getRange(3, 1);
  ctx.oskIssueSelectedRow();
  assert.match(ctx.alerts[0], /Настройка/);
  assert.equal(sheet.get(3, 'Номер билета').v, '');
});

test('отчёт: оплатой считается только статус «Оплачено», тесты исключены', () => {
  const pre = row({ 'Фамилия': 'Без', 'Имя': 'Оплаты', 'Телефон': '+996555', 'Сумма': 3000,
    'Ссылка на чек': 'https://drive/z', 'Номер билета': 'OSK-0011' });
  const testRow = row({ 'Фамилия': 'Тест', 'Имя': 'Тестов', 'Сумма': 3000, 'Статус оплаты': 'Оплачено',
    'Источник': 'test' });
  const { sheet, ctx } = setupSheet([single, pre, testRow]);
  tick(ctx, sheet, 3);
  const r = ctx.oskSalesReport_(ctx.oskReadModel_(sheet));
  assert.equal(r.paid, 2); // строка 2 (OSK-0050) и выданный OSK-0051
  assert.equal(r.revenue, 6000);
  assert.equal(r.sent, 1);
  assert.equal(r.notSent, 1);
  assert.equal(r.pending, 1); // номер есть, но оплата не подтверждена
  assert.equal(r.test, 1);
});

test('разбор денег и телефонов', () => {
  const ctx = load(baseSheet());
  assert.equal(ctx.oskParseMoney_('3 000 сом'), 3000);
  assert.equal(ctx.oskParseMoney_('3000,00'), 3000);
  assert.equal(ctx.oskParseMoney_('3,000'), 3000);
  assert.equal(ctx.oskParseMoney_(3000), 3000);
  assert.equal(ctx.oskPhoneDigits_('0555 12-34-56'), '996555123456');
  assert.equal(ctx.oskPhoneDigits_('+7 701 123 4567'), '77011234567');
});
