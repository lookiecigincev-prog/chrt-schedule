"""
Парсер расписания ЧРТ — использует Playwright (работает в GitHub Actions CI)
Установка: pip install playwright && playwright install chromium
"""
import os
import json
import re
import time
from datetime import date, timedelta
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ── НАСТРОЙКИ ─────────────────────────────────────────────────────────────────
BASE_URL     = "https://poo.edu-74.ru"
SCHEDULE_URL = f"{BASE_URL}/schedule/#/timetable"
LOGIN    = os.environ.get("CHRT_LOGIN",    "")
PASSWORD = os.environ.get("CHRT_PASSWORD", "")

if not LOGIN or not PASSWORD:
    raise Exception(
        "Логин и пароль не заданы!\n"
        "Задайте переменные среды CHRT_LOGIN и CHRT_PASSWORD.\n"
        "В GitHub: Settings → Secrets → CHRT_LOGIN и CHRT_PASSWORD"
    )

IS_CI = os.environ.get("CI") == "true"

# Паузы (в секундах)
WAIT_FOR_H2    = 3000   # мс — ждать название группы при переборе
WAIT_FOR_WEEK  = 20000  # мс — ждать появления расписания
BETWEEN_GROUPS = 2      # сек — пауза между группами

# ── УТИЛИТЫ ───────────────────────────────────────────────────────────────────
MONTHS = {
    'янв': 1, 'фев': 2, 'мар': 3, 'апр': 4, 'май': 5, 'июн': 6,
    'июл': 7, 'авг': 8, 'сен': 9, 'окт': 10, 'ноя': 11, 'дек': 12
}

def parse_date_from_week_header(header_text):
    match = re.search(r'с\s+(\d+)\s+(\S+?)\.\s+(\d{4})', header_text)
    if match:
        day, mon_str, year = int(match.group(1)), match.group(2).lower(), int(match.group(3))
        month = MONTHS.get(mon_str)
        if month:
            return f"{year}-{month:02d}-{day:02d}"
    return None

def get_date_for_day(week_start_str, day_short_name):
    DAYS_ORDER = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
    year, month, day = map(int, week_start_str.split('-'))
    monday = date(year, month, day)
    try:
        return (monday + timedelta(days=DAYS_ORDER.index(day_short_name))).isoformat()
    except ValueError:
        return week_start_str

# ── ЗАКРЫТИЕ МОДАЛЬНЫХ ОКОН ───────────────────────────────────────────────────
def close_modals(page):
    """Закрывает все модальные окна ошибок. Возвращает True если окно было."""
    found = False
    modals = page.query_selector_all('div.placeholder div.litebox')
    for modal in modals:
        try:
            if not modal.is_visible():
                continue
            found = True
            try:
                text = modal.query_selector('p.big')
                if text:
                    print(f"  ⚠️  {text.inner_text().strip()}")
            except Exception:
                pass
            for sel in ('span.close', 'button'):
                try:
                    btn = modal.query_selector(sel)
                    if btn:
                        btn.click()
                        break
                except Exception:
                    pass
            page.wait_for_timeout(300)
        except Exception:
            pass
    return found

# ── ОБНАРУЖЕНИЕ ГРУПП ─────────────────────────────────────────────────────────
def discover_groups(page):
    print("\n── Обнаружение групп (перебор ID 1–100) ────────────────")
    groups = {}

    for gid in range(1, 101):
        page.goto(f"{BASE_URL}/schedule/#/timetable/studentGroup/{gid}", wait_until='domcontentloaded')

        # Ждём появления ссылки с непустым именем группы
        try:
            page.wait_for_function(
                """() => {
                    const a = document.querySelector('hgroup h2 a.ng-binding');
                    return a && a.innerText.trim() && a.innerText.trim().toLowerCase() !== 'группа';
                }""",
                timeout=WAIT_FOR_H2
            )
            link = page.query_selector('hgroup h2 a.ng-binding')
            raw  = link.inner_text().strip()
            name = re.sub(r'^Группа\s+', '', raw, flags=re.IGNORECASE).strip()

            if close_modals(page):
                print(f"  ❌ {name} ({gid}): ошибка загрузки, пропускаем")
                continue

            groups[name] = gid
            print(f"  ✅ {name}: {gid}")
        except PWTimeout:
            print(f"  {gid}: пропускаем")

    return groups

# ── ПАРСИНГ РАСПИСАНИЯ ОДНОЙ ГРУППЫ ──────────────────────────────────────────
def parse_group_schedule(page, group_name, group_id):
    print(f"\n  Парсим {group_name} (id={group_id})...")
    page.goto(f"{BASE_URL}/schedule/#/timetable/studentGroup/{group_id}", wait_until='domcontentloaded')

    # Ждём расписание или ошибку
    try:
        page.wait_for_function(
            "() => document.querySelector('div.week') || document.querySelector('div.placeholder div.litebox')",
            timeout=WAIT_FOR_WEEK
        )
    except PWTimeout:
        print(f"  ❌ Таймаут, пропускаем")
        return []

    page.wait_for_timeout(500)

    if close_modals(page):
        print(f"  ❌ Ошибка загрузки расписания, пропускаем")
        return []

    page.wait_for_timeout(1500)

    lessons = []
    counter = 0

    week_blocks = page.query_selector_all('div.week')
    for week in week_blocks:
        week_start = None
        try:
            h4 = week.query_selector('h4')
            if h4:
                week_start = parse_date_from_week_header(h4.inner_text())
        except Exception:
            pass

        for day_el in week.query_selector_all('dl[x-ng-repeat="day in week"]'):
            day_short = ''
            try:
                big = day_el.query_selector('dt big')
                if big:
                    day_short = big.inner_text().strip()
            except Exception:
                pass

            lesson_date = get_date_for_day(week_start, day_short) if week_start else ''

            for event in day_el.query_selector_all('div.event'):
                pair_number = start_time = end_time = ''
                try:
                    num = event.query_selector('div.time div')
                    if num:
                        pair_number = num.inner_text().strip()
                except Exception:
                    pass
                try:
                    small = event.query_selector('div.time small')
                    if small:
                        raw   = small.inner_text().strip()
                        clean = raw.replace('–', '-').replace('—', '-')
                        if '-' in clean:
                            start_time, end_time = [p.strip() for p in clean.split('-', 1)]
                except Exception:
                    pass

                lesson_divs = event.query_selector_all('div.lessons div.lesson')
                if not lesson_divs:
                    continue

                for ld in lesson_divs:
                    subject = classroom = teacher = ''
                    try:
                        el = ld.query_selector('span.subject')
                        if el: subject = el.inner_text().strip()
                    except Exception:
                        pass
                    try:
                        el = ld.query_selector('small.classroom')
                        if el: classroom = el.inner_text().strip().replace('ауд. ', '')
                    except Exception:
                        pass
                    try:
                        el = ld.query_selector('small.teacher')
                        if el: teacher = el.inner_text().strip()
                    except Exception:
                        pass

                    if not subject:
                        continue

                    counter += 1
                    lessons.append({
                        'id':          str(counter),
                        'date':        lesson_date,
                        'number':      pair_number,
                        'startTime':   f"{lesson_date}T{start_time}:00" if lesson_date and start_time else start_time,
                        'endTime':     f"{lesson_date}T{end_time}:00"   if lesson_date and end_time   else end_time,
                        'subjectName': subject,
                        'type':        '',
                        'roomName':    classroom,
                        'teacherName': teacher,
                        'className':   group_name,
                    })

    print(f"  → {len(lessons)} уроков")
    return lessons

# ── MAIN ──────────────────────────────────────────────────────────────────────
with sync_playwright() as pw:
    print("── Запуск браузера ──────────────────────────────────────")
    browser = pw.chromium.launch(
        headless=True,
        args=[
            '--no-sandbox',
            '--disable-dev-shm-usage',
            '--disable-gpu',
            '--disable-setuid-sandbox',
            '--no-zygote',
            '--single-process',
        ]
    )
    context = browser.new_context(
        viewport={'width': 1280, 'height': 900},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'
    )
    page = context.new_page()
    page.set_default_timeout(30000)

    try:
        # ── Авторизация ───────────────────────────────────────────────────────
        print("── Авторизация ──────────────────────────────────────────")
        page.goto(SCHEDULE_URL, wait_until='domcontentloaded')

        # Снимаем ng-hide с формы
        page.add_style_tag(content='.ng-hide { display: block !important; }')

        page.wait_for_selector('form[name="formAuth"]', timeout=15000)
        page.wait_for_timeout(1500)

        # Заполняем форму
        form = page.query_selector('form[name="formAuth"]')
        for inp in form.query_selector_all('input'):
            t = inp.get_attribute('type') or ''
            n = inp.get_attribute('name') or ''
            if t == 'text'     or n == 'login':    inp.fill(LOGIN)
            if t == 'password' or n == 'password': inp.fill(PASSWORD)

        # Клик по кнопке submit
        try:
            form.query_selector('button[type="submit"]').click()
        except Exception:
            page.evaluate("document.querySelector('form[name=\"formAuth\"]').submit()")

        # Ждём редиректа
        page.wait_for_function(
            f"() => window.location.href !== '{SCHEDULE_URL}'",
            timeout=15000
        )
        page.wait_for_timeout(2000)
        print(f"✅ Авторизован. URL: {page.url}")

        # ── Обнаружение групп ─────────────────────────────────────────────────
        groups = discover_groups(page)

        if not groups:
            raise Exception("Не найдено ни одной группы")

        print(f"\n✅ Групп найдено: {len(groups)}")
        for name, gid in sorted(groups.items()):
            print(f"   {name}: {gid}")

        # ── Парсинг расписания ────────────────────────────────────────────────
        print("\n── Парсинг расписания ───────────────────────────────────")
        schedule_data = {}
        for group_name, group_id in sorted(groups.items()):
            schedule_data[group_name] = parse_group_schedule(page, group_name, group_id)
            time.sleep(BETWEEN_GROUPS)

        # ── Сохранение ────────────────────────────────────────────────────────
        with open('schedule.json', 'w', encoding='utf-8') as f:
            json.dump(schedule_data, f, ensure_ascii=False, indent=4)

        total = sum(len(v) for v in schedule_data.values())
        print(f"\n✅ Готово! {total} уроков, {len(schedule_data)} групп → schedule.json")

    except Exception as e:
        import traceback
        print(f"\n❌ Ошибка: {e}")
        traceback.print_exc()
        browser.close()
        exit(1)

    browser.close()
