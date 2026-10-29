// Проверка скрипта в имитации браузера. Запуск: npm i --no-save jsdom@24 && node osk-tilda/test/browser-check.js
const { JSDOM } = require('jsdom');
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
const block = fs.readFileSync(path.join(__dirname, '..', 'osk-forum-price-whatsapp.html'), 'utf8');
const script = block.match(/<script>([\s\S]*)<\/script>/)[1];

async function page(nowIso) {
  const html = `<!doctype html><body>
    <a id="nav" href="#ticket">Билет</a>
    <a id="hero" href="#ticket" class="tn-atom"><span>Купить билет</span></a>
    <div id="rec4634692503"><span>Скидка 50%</span><span>Сейчас к оплате: 3 000 сом</span></div>
    <a name="ticket"></a>
    <div id="rec4634694403"><div class="t396"><div class="t396__artboard">
      <div class="tn-atom" field="tn_text_1791545610000000006">Цена растёт: до 19.10 — 3 000…</div>
      <a id="buy" href="#popup:payment" class="tn-atom">Купить через WhatsApp</a>
    </div></div></div>
    <form data-success-url="https://wa.me/?text=old"></form></body>`;
  const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'https://osk.kg/ticket' });
  const w = dom.window;
  w.Date.now = () => Date.parse(nowIso);
  const nav = [], events = [];
  let popupOpened = 0;
  // Обработчик Тильды на кнопке попапа
  w.document.getElementById('buy').addEventListener('click', (e) => { e.preventDefault(); popupOpened++; });
  w.gtag = (...a) => events.push(a);
  w.setTimeout = (fn) => fn();
  w.__nav = (v) => nav.push(v);
  w.eval(script.replace(/window\.location\.href = waHref/, 'window.__nav(waHref)'));
  await new Promise((r) => w.document.readyState === 'loading' ? w.document.addEventListener('DOMContentLoaded', r) : r());
  return { w, d: w.document, nav, events, popup: () => popupOpened };
}

const click = (p, el) => el.dispatchEvent(new p.w.MouseEvent('click', { bubbles: true, cancelable: true }));
const decoded = (h) => decodeURIComponent(h.split('text=')[1]);

(async () => {
  const p = await page('2026-10-29T10:00:00+06:00');
  const d = p.d;

  // Новый блок «Билет участника»
  const sec = d.querySelector('#rec4634694403 .osk-tk');
  assert.ok(sec, 'новый блок вставлен');
  assert.match(d.getElementById('osk-tk-css').textContent, /#rec4634694403 \.t396\{display:none!important\}/, 'Zero Block спрятан');
  assert.equal(sec.querySelector('.osk-tk__badge').textContent, '−33%');
  assert.equal(sec.querySelector('.osk-tk__old').textContent, '6 000 сом');
  assert.equal(sec.querySelector('.osk-tk__price').textContent, '4 000 сом');
  assert.match(sec.querySelector('.osk-tk__until').textContent, /до 2 ноября, 23:59\. Потом — 5 000 сом/);
  assert.ok(!sec.textContent.includes('Цена растёт'), 'строки «Цена растёт» в новом блоке нет');
  const btn = sec.querySelector('.osk-tk__btn');
  assert.equal(btn.textContent, 'Купить билет за 4 000 сом');
  assert.ok(btn.href.startsWith('https://wa.me/996502510770?text='));
  console.log('Текст покупателя:\n' + decoded(btn.href));
  assert.match(decoded(btn.href), /4 000 сом \(до 2 ноября\)/);

  // Кнопки ведут в WhatsApp, попап не открывается, меню не трогаем
  const hero = d.getElementById('hero'), buy = d.getElementById('buy'), navLink = d.getElementById('nav');
  assert.ok(hero.href.startsWith('https://wa.me/996502510770?text='));
  assert.equal(navLink.getAttribute('href'), '#ticket');
  assert.equal(d.querySelector('form').getAttribute('data-success-url'), btn.href);
  click(p, btn);
  click(p, buy);
  click(p, hero.querySelector('span'));
  click(p, navLink);
  assert.equal(p.popup(), 0, 'попап формы не открылся');
  assert.equal(p.nav.length, 3, 'три кнопки покупки ведут в WhatsApp, меню — нет');
  assert.equal(p.events.length, 3);

  // Повторный запуск (Тильда перерисовала страницу) не дублирует блок
  p.w.dispatchEvent(new p.w.Event('load'));
  assert.equal(d.querySelectorAll('.osk-tk').length, 1);

  // Последний этап: без скидки — без бейджа и зачёркнутой цены
  const late = await page('2026-11-20T12:00:00+06:00');
  const s2 = late.d.querySelector('.osk-tk');
  assert.equal(s2.querySelector('.osk-tk__badge'), null);
  assert.equal(s2.querySelector('.osk-tk__old'), null);
  assert.equal(s2.querySelector('.osk-tk__price').textContent, '6 000 сом');
  assert.match(s2.querySelector('.osk-tk__until').textContent, /^Цена действует до 21 ноября$/);
  console.log('\nВсе проверки прошли');
})().catch((e) => { console.error(e); process.exit(1); });
