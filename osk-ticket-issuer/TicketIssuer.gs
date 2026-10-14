/**
 * OSK Forum 2026 — выдача билетов флажком прямо в таблице.
 *
 * Скрипт привязан к таблице (Расширения → Apps Script) и НЕ трогает
 * опубликованный Web App приема чеков. Он только дописывает в лист
 * «Билеты и оплаты» номер билета, ссылки и статус — те же поля, что
 * пишет ticket_tracker.js --confirm-row, поэтому трекер дальше работает
 * как раньше (ticket_ready → уведомление в Telegram).
 *
 * Как пользоваться:
 *   ✅ Выдать билеты  — флажок на любой строке группы: проверка группы,
 *                       номера OSK-xxxx всем гостям, «Оплачено», ссылки.
 *   📤 Отправлено     — флажок после реальной отправки в WhatsApp: пишет SENT.
 *   Результат выдачи  — что сделал скрипт или почему отказал.
 */

var OSK = {
  SHEET_NAME: 'Билеты и оплаты',
  TICKET_PRICE: 3000,
  TICKET_PAGE_URL: 'https://osk-forum-2026.baktiiar.chatgpt.site/ticket-demo.html',
  TICKET_PREFIX: 'OSK-',
  TICKET_DIGITS: 4,
  // 'first_last' → «Имя Фамилия», 'last_first' → «Фамилия Имя».
  // Сверьте с уже выданной ссылкой билета и при необходимости поменяйте.
  NAME_ORDER: 'first_last',
  SALES_GOAL: 500,
  PAID_STATUS: 'Оплачено',
  MANAGER_ACTION: 'Подтвердить',
  SENT_MARK: 'SENT',
  ISSUE_HEADER: '✅ Выдать билеты',
  SENT_HEADER: '📤 Отправлено',
  RESULT_HEADER: 'Результат выдачи',
  COUNTER_PROPERTY: 'OSK_LAST_TICKET_NUMBER',
  LOCK_WAIT_MS: 30000,
  WHATSAPP_TEXT:
    'Здравствуйте, {name}! Оплата подтверждена, ваш билет на OSK Forum 2026 ' +
    '(21 ноября, 10:00, Бишкек, Хан-Тенгри).\n' +
    'Номер билета: {number}\n' +
    'Билет с QR-кодом: {url}\n' +
    'Покажите QR-код на входе.',
  TIMEZONE: 'Asia/Bishkek'
};

// Ключ → возможные названия колонки (без учета регистра, ё = е).
var OSK_COLUMNS = {
  appNumber: { required: false, names: ['Номер заявки'] },
  lastName: { required: true, names: ['Фамилия'] },
  firstName: { required: true, names: ['Имя'] },
  phone: { required: true, names: ['Телефон', 'WhatsApp', 'Телефон WhatsApp'] },
  amount: { required: true, names: ['Сумма'] },
  receipt: { required: true, names: ['Ссылка на чек', 'Чек'] },
  action: { required: false, names: ['Действие менеджера'] },
  source: { required: false, names: ['Источник'] },
  payStatus: { required: true, names: ['Статус оплаты'] },
  ticketNumber: { required: true, names: ['Номер билета'] },
  ticketLink: { required: true, names: ['Ссылка на билет'] },
  ticketCopy: { required: false, names: ['Ссылка билета для копирования'] },
  whatsapp: { required: false, names: ['WhatsApp с билетом'] },
  paymentId: { required: false, names: ['ID оплаты'] },
  groupCount: { required: false, names: ['Кол-во билетов', 'Количество билетов'] },
  groupIndex: { required: false, names: ['№ билета в оплате', 'Номер билета в оплате'] },
  issue: { required: false, names: [OSK.ISSUE_HEADER] },
  sent: { required: false, names: [OSK.SENT_HEADER] },
  result: { required: false, names: [OSK.RESULT_HEADER] }
};

// ---------------------------------------------------------------------------
// Меню и триггеры

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('OSK Билеты')
    .addItem('Выдать билеты для выделенной строки', 'oskIssueSelectedRow')
    .addItem('Отчёт продаж', 'oskShowSalesReport')
    .addSeparator()
    .addItem('Настройка (один раз)', 'oskSetup')
    .addToUi();
  try {
    var sheet = oskGetSheet_();
    var map = oskBuildColumnMap_(oskReadHeaders_(sheet));
    if (map.cols.issue && map.cols.sent) oskEnsureCheckboxes_(sheet, map.cols);
  } catch (err) {
    // До первой настройки колонок нет — меню всё равно должно появиться.
  }
}

/** Добавляет служебные колонки, флажки и триггер. Повторный запуск безопасен. */
function oskSetup() {
  var ui = SpreadsheetApp.getUi();
  var sheet = oskGetSheet_();
  var headers = oskReadHeaders_(sheet);
  var map = oskBuildColumnMap_(headers);
  if (map.missing.length) {
    ui.alert('Не найдены колонки: ' + map.missing.join(', ') +
      '.\nНастройка остановлена, таблица не изменена.');
    return;
  }

  var lastHeaderCol = oskLastHeaderColumn_(headers);
  var added = [];
  [OSK.ISSUE_HEADER, OSK.SENT_HEADER, OSK.RESULT_HEADER].forEach(function (title) {
    if (oskFindColumn_(headers, [title]) === -1) {
      lastHeaderCol += 1;
      if (lastHeaderCol > sheet.getMaxColumns()) {
        sheet.insertColumnAfter(sheet.getMaxColumns());
      }
      sheet.getRange(1, lastHeaderCol).setValue(title);
      headers[lastHeaderCol - 1] = title;
      added.push(title);
    }
  });

  map = oskBuildColumnMap_(headers);
  oskEnsureCheckboxes_(sheet, map.cols);

  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'oskOnEdit') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('oskOnEdit')
    .forSpreadsheet(SpreadsheetApp.getActive())
    .onEdit()
    .create();

  var model = oskReadModel_(sheet);
  var props = PropertiesService.getDocumentProperties();
  var maxNumber = Math.max(oskMaxTicketNumber_(model), Number(props.getProperty(OSK.COUNTER_PROPERTY)) || 0);
  props.setProperty(OSK.COUNTER_PROPERTY, String(maxNumber));

  ui.alert('Готово.\n' +
    (added.length ? 'Добавлены колонки: ' + added.join(', ') + '.\n' : 'Колонки уже были.\n') +
    'Последний выданный номер: ' + oskFormatTicket_(maxNumber) + '.\n' +
    'Флажок «' + OSK.ISSUE_HEADER + '» теперь выдаёт билеты, в том числе с телефона.');
}

/** Installable onEdit (ставится в oskSetup). Срабатывает и в мобильном приложении. */
function oskOnEdit(e) {
  if (!e || !e.range) return;
  var sheet = e.range.getSheet();
  if (sheet.getName() !== OSK.SHEET_NAME) return;
  if (e.range.getRow() < 2) return;

  var map = oskBuildColumnMap_(oskReadHeaders_(sheet));
  if (!map.cols.issue || !map.cols.sent || !map.cols.result) return;

  var firstCol = e.range.getColumn();
  var lastCol = firstCol + e.range.getNumColumns() - 1;
  var actor = oskActor_(e);
  [['issue', oskIssueRow_], ['sent', oskMarkSentRow_]].forEach(function (pair) {
    var col = map.cols[pair[0]];
    if (col < firstCol || col > lastCol) return;
    var values = sheet.getRange(e.range.getRow(), col, e.range.getNumRows(), 1).getValues();
    for (var i = 0; i < values.length; i++) {
      if (values[i][0] === true) pair[1](sheet, e.range.getRow() + i, actor);
    }
  });
}

function oskIssueSelectedRow() {
  var sheet = oskGetSheet_();
  var active = SpreadsheetApp.getActiveSheet();
  var range = SpreadsheetApp.getActiveRange();
  var row = active.getName() === OSK.SHEET_NAME && range ? range.getRow() : 0;
  if (row < 2) {
    SpreadsheetApp.getUi().alert('Выделите строку заявки на листе «' + OSK.SHEET_NAME + '».');
    return;
  }
  var result = oskIssueRow_(sheet, row, oskActor_(null));
  SpreadsheetApp.getUi().alert(result.message);
}

// ---------------------------------------------------------------------------
// Выдача билетов

function oskIssueRow_(sheet, row, actor) {
  var cols = oskBuildColumnMap_(oskReadHeaders_(sheet)).cols;
  if (!cols.issue || !cols.result) {
    return { ok: false, message: 'Сначала запустите «OSK Билеты → Настройка (один раз)».' };
  }
  var lock = LockService.getDocumentLock();
  if (!lock.tryLock(OSK.LOCK_WAIT_MS)) {
    return oskReject_(sheet, row, 'Другая выдача ещё идёт. Поставьте флажок ещё раз через минуту.');
  }
  try {
    var model = oskReadModel_(sheet);
    var props = PropertiesService.getDocumentProperties();
    var plan = oskPlanIssue_(model, row, {
      lastIssued: Number(props.getProperty(OSK.COUNTER_PROPERTY)) || 0,
      actor: actor,
      now: Utilities.formatDate(new Date(), OSK.TIMEZONE, 'dd.MM HH:mm')
    });
    if (!plan.ok) return oskReject_(sheet, row, plan.message, model);

    oskApplyWrites_(sheet, plan.writes);
    if (plan.lastIssued) props.setProperty(OSK.COUNTER_PROPERTY, String(plan.lastIssued));
    SpreadsheetApp.flush();
    oskEnsureCheckboxes_(sheet, model.cols);
    return plan;
  } finally {
    lock.releaseLock();
  }
}

function oskMarkSentRow_(sheet, row, actor) {
  var model = oskReadModel_(sheet);
  var r = model.byRow[row];
  var cols = model.cols;
  var stamp = Utilities.formatDate(new Date(), OSK.TIMEZONE, 'dd.MM HH:mm');
  if (!r || !oskParseTicket_(r.ticketNumber)) {
    sheet.getRange(row, cols.sent).setValue(false);
    sheet.getRange(row, cols.result).setValue('❌ ' + stamp + ' Нельзя отметить отправку: билет ещё не выдан.');
    return;
  }
  if (cols.whatsapp) sheet.getRange(row, cols.whatsapp).setValue(OSK.SENT_MARK);
  sheet.getRange(row, cols.result).setValue(
    '📤 ' + stamp + ' ' + r.ticketNumber + ' отмечен отправленным' + (actor ? ' (' + actor + ')' : ''));
}

function oskReject_(sheet, row, message, model) {
  var cols = (model || oskReadModel_(sheet)).cols;
  var stamp = Utilities.formatDate(new Date(), OSK.TIMEZONE, 'dd.MM HH:mm');
  sheet.getRange(row, cols.issue).setValue(false);
  sheet.getRange(row, cols.result).setValue('❌ ' + stamp + ' ' + message);
  return { ok: false, message: message };
}

/**
 * Чистая логика: что записать, чтобы выдать билеты группе строки `row`.
 * Ничего не пишет сама — возвращает список записей. Покрыта тестами.
 */
function oskPlanIssue_(model, row, opts) {
  var target = model.byRow[row];
  if (!target) return oskFail_('Строка ' + row + ' пустая.');

  var group = oskFindGroup_(model, target);
  var count = group.length;
  var declared = 0;
  group.forEach(function (r) { declared = Math.max(declared, oskParseInt_(r.groupCount)); });
  if (declared && declared !== count) {
    return oskFail_('В оплате указано билетов: ' + declared + ', а строк с этим чеком: ' + count +
      '. Проверьте группу (ID оплаты / ссылку на чек).');
  }
  if (count > 6) return oskFail_('В группе ' + count + ' строк — больше 6. Проверьте ID оплаты.');

  var problems = [];
  var receiptMissing = group.every(function (r) { return !r.receipt; });
  if (receiptMissing) problems.push('нет ссылки на чек');
  group.forEach(function (r) {
    var where = 'стр. ' + r.row + ': ';
    if (/отклон|возврат|отмен/i.test(r.payStatus)) problems.push(where + 'статус «' + r.payStatus + '»');
    if (oskLetters_(r.lastName) < 2 || oskLetters_(r.firstName) < 2) problems.push(where + 'нет фамилии или имени');
    if (oskPhoneDigits_(r.phone).length < 11) problems.push(where + 'неполный телефон');
    if (oskParseMoney_(r.amount) !== OSK.TICKET_PRICE) {
      problems.push(where + 'сумма «' + r.amount + '», ожидалось ' + OSK.TICKET_PRICE);
    }
    if (r.ticketNumber && !oskParseTicket_(r.ticketNumber)) {
      problems.push(where + 'в «Номер билета» стоит «' + r.ticketNumber + '»');
    }
  });
  if (problems.length) return oskFail_('Не выдано: ' + problems.join('; ') + '.');

  var paid = oskNormalize_(OSK.PAID_STATUS);
  var todo = group.filter(function (r) { return !r.ticketNumber || oskNormalize_(r.payStatus) !== paid; });
  var expectedTotal = OSK.TICKET_PRICE * count;
  var groupLabel = count > 1 ? 'Группа ' + count + ' бил., чек ' + oskMoney_(expectedTotal) + ' сом' : 'Чек ' + oskMoney_(expectedTotal) + ' сом';

  if (!todo.length) {
    var existing = group.map(function (r) { return r.ticketNumber; }).join(', ');
    return { ok: true, writes: [], lastIssued: 0, numbers: [],
      message: 'Билеты уже выданы: ' + existing + '. Ничего не изменено.' };
  }

  var next = Math.max(oskMaxTicketNumber_(model), opts.lastIssued || 0);
  var cols = model.cols;
  var writes = [];
  var numbers = [];
  var who = opts.actor ? ' · ' + opts.actor : '';

  var lastIssued = 0;
  todo.forEach(function (r) {
    // Номер, проставленный заранее (fill_ticket_links.js), сохраняем — меняем только статус.
    var number = r.ticketNumber;
    if (!number) {
      next += 1;
      lastIssued = next;
      number = oskFormatTicket_(next);
      writes.push({ row: r.row, col: cols.ticketNumber, value: number });
    }
    numbers.push(number);

    var url = r.ticketLink;
    if (!(r.ticketNumber && /^https?:\/\//i.test(url))) {
      url = OSK.TICKET_PAGE_URL + '?ticket=' + encodeURIComponent(number) +
        '&name=' + encodeURIComponent(oskTicketName_(r));
      writes.push({ row: r.row, col: cols.ticketLink, formula: oskHyperlink_(url, number) });
      if (cols.ticketCopy) writes.push({ row: r.row, col: cols.ticketCopy, value: url });
    }
    var waText = OSK.WHATSAPP_TEXT
      .replace('{name}', r.firstName.trim())
      .replace('{number}', number)
      .replace('{url}', url);
    var waUrl = 'https://wa.me/' + oskPhoneDigits_(r.phone) + '?text=' + encodeURIComponent(waText);
    writes.push({ row: r.row, col: cols.payStatus, value: OSK.PAID_STATUS });
    if (cols.action) writes.push({ row: r.row, col: cols.action, value: OSK.MANAGER_ACTION });
    if (cols.whatsapp && r.whatsapp !== OSK.SENT_MARK) {
      writes.push({ row: r.row, col: cols.whatsapp, formula: oskHyperlink_(waUrl, 'Отправить в WhatsApp') });
    }
    writes.push({ row: r.row, col: cols.issue, value: true });
    writes.push({ row: r.row, col: cols.result,
      value: '✅ ' + opts.now + ' ' + number + ' · ' + groupLabel + who });
  });
  // Уже подтверждённые строки группы тоже помечаем флажком, чтобы группа выглядела единой.
  group.forEach(function (r) {
    if (todo.indexOf(r) === -1) writes.push({ row: r.row, col: cols.issue, value: true });
  });

  return {
    ok: true,
    writes: writes,
    numbers: numbers,
    lastIssued: lastIssued,
    message: 'Выдано: ' + numbers.join(', ') + '. ' + groupLabel + ' — сверьте с банком.'
  };
}

function oskFindGroup_(model, target) {
  var key;
  if (target.paymentId) {
    key = function (r) { return r.paymentId === target.paymentId; };
  } else if (target.receipt) {
    key = function (r) { return !r.paymentId && r.receipt === target.receipt; };
  } else {
    return [target];
  }
  return model.rows.filter(key).sort(function (a, b) {
    return (oskParseInt_(a.groupIndex) || 0) - (oskParseInt_(b.groupIndex) || 0) || a.row - b.row;
  });
}

function oskApplyWrites_(sheet, writes) {
  writes.forEach(function (w) {
    var cell = sheet.getRange(w.row, w.col);
    if (w.formula) cell.setFormula(w.formula);
    else cell.setValue(w.value);
  });
}

// ---------------------------------------------------------------------------
// Отчёт продаж: оплатой считается только «Статус оплаты» = «Оплачено».

function oskShowSalesReport() {
  var r = oskSalesReport_(oskReadModel_(oskGetSheet_()));
  SpreadsheetApp.getUi().alert(
    'Оплачено билетов: ' + r.paid + ' из ' + OSK.SALES_GOAL + ' (' + r.percent + '%)\n' +
    'Выручка по оплаченным: ' + oskMoney_(r.revenue) + ' сом\n' +
    'Отправлено в WhatsApp: ' + r.sent + '\n' +
    'Оплачено, но не отправлено: ' + r.notSent + '\n' +
    'Заявки без подтверждения: ' + r.pending + '\n' +
    'Тестовые строки не учтены: ' + r.test);
}

function oskSalesReport_(model) {
  var report = { paid: 0, revenue: 0, sent: 0, notSent: 0, pending: 0, test: 0, percent: 0 };
  model.rows.forEach(function (r) {
    if (!r.lastName && !r.firstName) return;
    if (/test|тест|demo|демо/i.test([r.appNumber, r.source, r.lastName, r.firstName].join(' '))) {
      report.test += 1;
      return;
    }
    if (oskNormalize_(r.payStatus) === oskNormalize_(OSK.PAID_STATUS)) {
      report.paid += 1;
      report.revenue += oskParseMoney_(r.amount);
      if (r.whatsapp === OSK.SENT_MARK) report.sent += 1;
      else report.notSent += 1;
    } else if (r.receipt && !/отклон|возврат|отмен/i.test(r.payStatus)) {
      report.pending += 1;
    }
  });
  report.percent = Math.round(report.paid * 1000 / OSK.SALES_GOAL) / 10;
  return report;
}

// ---------------------------------------------------------------------------
// Чтение листа

function oskGetSheet_() {
  var sheet = SpreadsheetApp.getActive().getSheetByName(OSK.SHEET_NAME);
  if (!sheet) throw new Error('Нет листа «' + OSK.SHEET_NAME + '».');
  return sheet;
}

function oskReadHeaders_(sheet) {
  return sheet.getRange(1, 1, 1, sheet.getLastColumn()).getDisplayValues()[0];
}

/** Модель листа: строки с нужными полями. Ссылки берутся из HYPERLINK или rich text. */
function oskReadModel_(sheet) {
  var lastRow = sheet.getLastRow();
  var lastCol = sheet.getLastColumn();
  var headers = sheet.getRange(1, 1, 1, lastCol).getDisplayValues()[0];
  var map = oskBuildColumnMap_(headers);
  if (map.missing.length) throw new Error('Не найдены колонки: ' + map.missing.join(', '));
  if (lastRow < 2) return oskBuildModel_(map.cols, [], [], [], []);

  var range = sheet.getRange(2, 1, lastRow - 1, lastCol);
  var receiptRich = sheet.getRange(2, map.cols.receipt, lastRow - 1, 1).getRichTextValues()
    .map(function (row) { return row[0] && row[0].getLinkUrl ? row[0].getLinkUrl() || '' : ''; });
  return oskBuildModel_(map.cols, range.getValues(), range.getDisplayValues(), range.getFormulas(), receiptRich);
}

function oskBuildModel_(cols, values, display, formulas, receiptRich) {
  var rows = [];
  var byRow = {};
  for (var i = 0; i < display.length; i++) {
    var get = function (key) {
      return cols[key] ? String(display[i][cols[key] - 1] || '').trim() : '';
    };
    var r = {
      row: i + 2,
      appNumber: get('appNumber'),
      lastName: get('lastName'),
      firstName: get('firstName'),
      phone: get('phone'),
      amount: get('amount'),
      receipt: oskLinkOf_(display[i][cols.receipt - 1], formulas[i][cols.receipt - 1], receiptRich[i]),
      source: get('source'),
      payStatus: get('payStatus'),
      ticketNumber: get('ticketNumber'),
      ticketLink: cols.ticketLink
        ? oskLinkOf_(display[i][cols.ticketLink - 1], formulas[i][cols.ticketLink - 1], '')
        : '',
      whatsapp: get('whatsapp'),
      paymentId: get('paymentId'),
      groupCount: get('groupCount'),
      groupIndex: get('groupIndex')
    };
    if (!r.lastName && !r.firstName && !r.phone && !r.receipt && !r.ticketNumber && !r.paymentId) continue;
    rows.push(r);
    byRow[r.row] = r;
  }
  return { cols: cols, rows: rows, byRow: byRow };
}

/** Флажки на всех строках листа + запас пустых строк, чтобы новые заявки сразу были с флажками. */
function oskEnsureCheckboxes_(sheet, cols) {
  if (sheet.getMaxRows() - sheet.getLastRow() < 200) {
    sheet.insertRowsAfter(sheet.getMaxRows(), 500);
  }
  var rows = sheet.getMaxRows() - 1;
  var checkbox = SpreadsheetApp.newDataValidation().requireCheckbox().build();
  sheet.getRange(2, cols.issue, rows, 1).setDataValidation(checkbox);
  sheet.getRange(2, cols.sent, rows, 1).setDataValidation(checkbox);
}

function oskBuildColumnMap_(headers) {
  var cols = {};
  var missing = [];
  Object.keys(OSK_COLUMNS).forEach(function (key) {
    var idx = oskFindColumn_(headers, OSK_COLUMNS[key].names);
    if (idx === -1) {
      if (OSK_COLUMNS[key].required) missing.push(OSK_COLUMNS[key].names[0]);
    } else {
      cols[key] = idx + 1;
    }
  });
  return { cols: cols, missing: missing };
}

function oskFindColumn_(headers, names) {
  var wanted = names.map(oskNormalize_);
  for (var i = 0; i < headers.length; i++) {
    if (wanted.indexOf(oskNormalize_(headers[i])) !== -1) return i;
  }
  return -1;
}

function oskLastHeaderColumn_(headers) {
  for (var i = headers.length - 1; i >= 0; i--) {
    if (String(headers[i]).trim()) return i + 1;
  }
  return 0;
}

// ---------------------------------------------------------------------------
// Мелкие помощники

function oskFail_(message) {
  return { ok: false, message: message, writes: [] };
}

function oskNormalize_(s) {
  return String(s || '').trim().toLowerCase().replace(/ё/g, 'е').replace(/\s+/g, ' ');
}

function oskLinkOf_(display, formula, richUrl) {
  var m = /HYPERLINK\(\s*"([^"]+)"/i.exec(String(formula || ''));
  if (m) return m[1];
  if (richUrl) return richUrl;
  return String(display || '').trim();
}

function oskHyperlink_(url, label) {
  return '=HYPERLINK("' + String(url).replace(/"/g, '%22') + '","' + String(label).replace(/"/g, "'") + '")';
}

function oskParseTicket_(s) {
  var prefix = OSK.TICKET_PREFIX.replace(/[-]/g, '\\-');
  var m = new RegExp('^' + prefix + '(\\d+)$', 'i').exec(String(s || '').trim());
  return m ? Number(m[1]) : 0;
}

function oskFormatTicket_(n) {
  var s = String(n);
  while (s.length < OSK.TICKET_DIGITS) s = '0' + s;
  return OSK.TICKET_PREFIX + s;
}

function oskMaxTicketNumber_(model) {
  var max = 0;
  model.rows.forEach(function (r) { max = Math.max(max, oskParseTicket_(r.ticketNumber)); });
  return max;
}

function oskTicketName_(r) {
  var first = r.firstName.trim();
  var last = r.lastName.trim();
  return OSK.NAME_ORDER === 'last_first' ? last + ' ' + first : first + ' ' + last;
}

/** Цифры телефона для wa.me. Местный формат 0XXX XXX XXX → 996XXXXXXXXX. */
function oskPhoneDigits_(phone) {
  var d = String(phone || '').replace(/\D/g, '');
  if (d.length === 10 && d.charAt(0) === '0') return '996' + d.slice(1);
  if (d.length === 9) return '996' + d;
  return d;
}

function oskParseMoney_(v) {
  var s = String(v || '').replace(/[\s ]/g, '').replace(/[^\d.,]/g, '');
  s = s.replace(/[.,]\d{1,2}$/, '').replace(/[.,]/g, '');
  return s ? Number(s) : 0;
}

function oskParseInt_(v) {
  var n = parseInt(String(v || '').replace(/\D/g, ''), 10);
  return isNaN(n) ? 0 : n;
}

function oskLetters_(s) {
  var m = String(s || '').match(/[A-Za-zА-Яа-яЁёӨөҮүҢң]/g);
  return m ? m.length : 0;
}

function oskMoney_(n) {
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
}

function oskActor_(e) {
  try {
    if (e && e.user && e.user.getEmail && e.user.getEmail()) return e.user.getEmail();
    return Session.getActiveUser().getEmail() || '';
  } catch (err) {
    return '';
  }
}
