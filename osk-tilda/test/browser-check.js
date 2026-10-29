// Проверка скрипта в имитации браузера. Запуск: npm i --no-save jsdom@24 && node osk-tilda/test/browser-check.js
const { JSDOM } = require('jsdom');
const fs = require('fs');
const block = fs.readFileSync(require('path').join(__dirname, '..', 'osk-forum-price-whatsapp.html'), 'utf8');
const script = block.match(/<script>([\s\S]*)<\/script>/)[1];
const assert = require('assert/strict');

async function page(nowIso) {
  const html = `<!doctype html><body>
    <a id="nav" href="#ticket">Билет</a>
    <a id="hero" href="#ticket" class="tn-atom"><span>Купить билет</span></a>
    <div id="rec4634692503"><span>Скидка 50%</span><span>Сейчас к оплате: 3 000 сом</span></div>
    <a id="buy" href="#popup:payment" class="tn-atom">Купить через WhatsApp</a>
    <form data-success-url="https://wa.me/?text=old"></form>
    <div field="tn_text_1791568200000000001">old</div>
    <div class="tn-elem"><div class="tn-atom" field="tn_text_1791545610000000006">Цена растёт: до 19.10 — 3 000…</div></div></body>`;
  const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'https://osk.kg/ticket' });
  const w = dom.window;
  const realNow = w.Date.now; w.Date.now = () => Date.parse(nowIso);
  let popupOpened = 0, nav = [], events = [];
  // Обработчик Тильды на кнопке попапа
  w.document.getElementById('buy').addEventListener('click', (e) => { e.preventDefault(); popupOpened++; });
  w.gtag = (...a) => events.push(a);
  w.setTimeout = (fn) => fn();
  // перехват перехода
  const loc = { set href(v) { nav.push(v); }, get href() { return ''; } };
  w.eval(script.replace(/window\.location\.href = waHref/, 'window.__nav(waHref)'));
  w.__nav = (v) => nav.push(v);
  await new Promise((r) => w.document.readyState === 'loading' ? w.document.addEventListener('DOMContentLoaded', r) : r());
  return { w, d: w.document, nav, events, popup: () => popupOpened };
}

(async () => {
const p = await page('2026-10-27T10:00:00+06:00');
const d = p.d;
const hero = d.getElementById('hero'), buy = d.getElementById('buy'), navLink = d.getElementById('nav');
const decoded = (h) => decodeURIComponent(h.split('text=')[1]);
assert.ok(buy.href.startsWith('https://wa.me/996502510770?text='));
assert.ok(hero.href.startsWith('https://wa.me/996502510770?text='));
assert.equal(navLink.getAttribute('href'), '#ticket', 'меню «Билет» не трогаем');
console.log('Текст покупателя:\n' + decoded(buy.href));
assert.match(decoded(buy.href), /4 000 сом \(до 2 ноября\)/);
assert.equal(d.querySelector('form').getAttribute('data-success-url'), buy.href);
assert.match(d.querySelector('#rec4634692503').textContent, /Скидка 33%Сейчас к оплате: 4 000 сом/);

buy.dispatchEvent(new p.w.MouseEvent('click', { bubbles: true, cancelable: true }));
assert.equal(p.popup(), 0, 'попап формы не открылся');
assert.equal(p.nav.length, 1); assert.ok(p.nav[0].startsWith('https://wa.me/996502510770?text='));
hero.querySelector('span').dispatchEvent(new p.w.MouseEvent('click', { bubbles: true, cancelable: true }));
assert.equal(p.nav.length, 2, 'клик по тексту внутри кнопки тоже ведёт в WhatsApp');
navLink.dispatchEvent(new p.w.MouseEvent('click', { bubbles: true, cancelable: true }));
assert.equal(p.nav.length, 2, 'меню не уводит в WhatsApp');
assert.equal(p.events.length, 2);

const slot = d.querySelector('[field="tn_text_1791545610000000006"]');
const btn = slot.querySelector('a[data-osk-buy]');
assert.ok(btn, 'на месте «Цена растёт» появилась кнопка');
assert.equal(btn.textContent, 'Купить билет за 4\u00a0000 сом');
assert.ok(btn.href.startsWith('https://wa.me/996502510770?text='));
assert.ok(!d.body.textContent.includes('Цена растёт'), 'строки с лестницей цен нет');
p.w.eval('');
btn.dispatchEvent(new p.w.MouseEvent('click', { bubbles: true, cancelable: true }));
assert.equal(p.nav.length, 3, 'новая кнопка ведёт в WhatsApp');
// повторный запуск (Тильда перерисовала блок) не дублирует кнопку
p.w.dispatchEvent(new p.w.Event('load'));
assert.equal(slot.querySelectorAll('a').length, 1);
const late = await page('2026-11-20T12:00:00+06:00');
assert.match(decoded(late.d.getElementById('buy').href), /6 000 сом \(до 21 ноября\)/);
console.log('\nВсе проверки прошли');
})().catch((e) => { console.error(e); process.exit(1); });
