/**
 * Помощник бота @OSK_Tech_bot на стороне Google.
 * Сохраняет фото и PDF-отчёты в папку на Google Диске и ведёт таблицу рейтинга.
 *
 * Установка — см. README.md, раздел «Google Диск и Таблица».
 */

// Секрет должен совпадать с переменной APPS_SCRIPT_SECRET у бота.
const SECRET = 'ВСТАВЬТЕ_СЮДА_ДЛИННЫЙ_СЕКРЕТ';

// ID папки «Тех РЕЙТИНГ» (часть ссылки после /folders/).
const ROOT_FOLDER_ID = '1eg68fWCl3u5q67WSBJ8lJKR060mISpR8';

const SPREADSHEET_NAME = 'ОСК — Технический рейтинг';

function doPost(e) {
  let req;
  try {
    req = JSON.parse(e.postData.contents);
  } catch (err) {
    return json_({ ok: false, error: 'bad json' });
  }
  if (!req || req.secret !== SECRET) {
    return json_({ ok: false, error: 'forbidden' });
  }

  // Последовательная обработка: иначе параллельные загрузки создают папки-дубли.
  const lock = LockService.getScriptLock();
  lock.waitLock(60000);
  try {
    switch (req.action) {
      case 'ping':
        return json_({ ok: true, root: DriveApp.getFolderById(ROOT_FOLDER_ID).getName() });
      case 'folder':
        return json_(folderInfo_(folderByPath_(req.path)));
      case 'upload':
        return json_(upload_(req));
      case 'trash':
        DriveApp.getFileById(req.fileId).setTrashed(true);
        return json_({ ok: true });
      case 'appendRows':
        return json_(appendRows_(req));
      default:
        return json_({ ok: false, error: 'unknown action: ' + req.action });
    }
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}

function folderByPath_(path) {
  let folder = DriveApp.getFolderById(ROOT_FOLDER_ID);
  (path || []).forEach(function (raw) {
    const name = String(raw).trim() || 'Без названия';
    const found = folder.getFoldersByName(name);
    folder = found.hasNext() ? found.next() : folder.createFolder(name);
  });
  return folder;
}

function folderInfo_(folder) {
  return { ok: true, folderId: folder.getId(), folderUrl: folder.getUrl() };
}

function upload_(req) {
  const folder = folderByPath_(req.path);
  const blob = Utilities.newBlob(Utilities.base64Decode(req.data), req.mimeType, req.name);
  const file = folder.createFile(blob);
  return {
    ok: true,
    fileId: file.getId(),
    fileUrl: file.getUrl(),
    folderId: folder.getId(),
    folderUrl: folder.getUrl(),
  };
}

function spreadsheet_() {
  const props = PropertiesService.getScriptProperties();
  const id = props.getProperty('SPREADSHEET_ID');
  if (id) {
    try {
      const existing = SpreadsheetApp.openById(id);
      if (!DriveApp.getFileById(id).isTrashed()) return existing;
    } catch (err) {
      // таблицу удалили — создадим новую
    }
  }
  const ss = SpreadsheetApp.create(SPREADSHEET_NAME);
  DriveApp.getFileById(ss.getId()).moveTo(DriveApp.getFolderById(ROOT_FOLDER_ID));
  props.setProperty('SPREADSHEET_ID', ss.getId());
  return ss;
}

function appendRows_(req) {
  const ss = spreadsheet_();
  let sheet = ss.getSheetByName(req.sheet);
  if (!sheet) {
    sheet = ss.insertSheet(req.sheet);
    // Убираем пустой лист по умолчанию, который создаётся вместе с таблицей.
    ss.getSheets().forEach(function (s) {
      if (s.getName() !== req.sheet && s.getLastRow() === 0 && ss.getSheets().length > 1) {
        ss.deleteSheet(s);
      }
    });
  }
  const width = req.headers.length;
  sheet.getRange(1, 1, 1, width).setValues([req.headers]).setFontWeight('bold');
  sheet.setFrozenRows(1);
  if (req.rows && req.rows.length) {
    sheet.getRange(sheet.getLastRow() + 1, 1, req.rows.length, width).setValues(req.rows);
  }
  return { ok: true, spreadsheetUrl: ss.getUrl() };
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

/** Запустите эту функцию один раз вручную, чтобы выдать скрипту доступ к Диску и Таблицам. */
function authorize() {
  Logger.log(DriveApp.getFolderById(ROOT_FOLDER_ID).getName());
  Logger.log(spreadsheet_().getUrl());
}
