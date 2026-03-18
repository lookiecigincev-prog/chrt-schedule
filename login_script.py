from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
import time
import json
import re

# ── НАСТРОЙКИ ─────────────────────────────────────────────────────────────────
BASE_URL     = "https://poo.edu-74.ru"
SCHEDULE_URL = f"{BASE_URL}/schedule/#/timetable"
import os
LOGIN    = os.environ.get("CHRT_LOGIN",    "")
PASSWORD = os.environ.get("CHRT_PASSWORD", "")
if not LOGIN or not PASSWORD:
    raise Exception(
        "Логин и пароль не заданы!\n"
        "Задайте переменные среды CHRT_LOGIN и CHRT_PASSWORD.\n"
        "В GitHub: Settings → Secrets → CHRT_LOGIN и CHRT_PASSWORD"
    )

# Паузы (в секундах)
WAIT_FOR_H2    = 2    # максимум ждать названия группы при переборе (короткий!)
WAIT_FOR_WEEK  = 15   # максимум ждать расписания при парсинге
BETWEEN_GROUPS = 2    # пауза между группами при парсинге

# ── CHROME ────────────────────────────────────────────────────────────────────
chrome_options = Options()
# chrome_options.add_argument("--headless")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")

service = Service(ChromeDriverManager().install())
driver  = webdriver.Chrome(service=service, options=chrome_options)
driver.set_page_load_timeout(30)

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
    from datetime import date, timedelta
    year, month, day = map(int, week_start_str.split('-'))
    monday = date(year, month, day)
    try:
        return (monday + timedelta(days=DAYS_ORDER.index(day_short_name))).isoformat()
    except ValueError:
        return week_start_str

# ── ОБНАРУЖЕНИЕ ГРУПП (перебор ID) ───────────────────────────────────────────
def discover_groups():
    """
    Перебираем ID 1–100. На каждой странице ждём появления ссылки
    с непустым названием группы. Таймаут короткий (2с) — несуществующие
    ID отсеиваются быстро, существующие загружаются за это время.
    """
    print("\n── Обнаружение групп (перебор ID 1–100) ────────────────")
    groups = {}

    def group_name_loaded(d):
        """Условие: ссылка с названием группы загружена и непустая."""
        try:
            link = d.find_element(By.CSS_SELECTOR, 'hgroup h2 a.ng-binding')
            text = link.text.strip()
            # Текст должен быть непустым и содержать что-то кроме слова "Группа"
            return text and text.lower() != 'группа'
        except Exception:
            return False

    for gid in range(1, 101):
        driver.get(f"{BASE_URL}/schedule/#/timetable/studentGroup/{gid}")
        try:
            WebDriverWait(driver, WAIT_FOR_H2).until(group_name_loaded)
            link = driver.find_element(By.CSS_SELECTOR, 'hgroup h2 a.ng-binding')
            raw  = link.text.strip()
            name = re.sub(r'^Группа\s+', '', raw, flags=re.IGNORECASE).strip()
            # Проверяем не вылетела ли сразу ошибка загрузки расписания
            if close_error_modal():
                print(f"  ❌ {name} ({gid}): ошибка загрузки, пропускаем")
            else:
                groups[name] = gid
                print(f"  ✅ {name}: {gid}")
        except TimeoutException:
            print(f"  {gid}: пропускаем")

    return groups

# ── ПАРСИНГ РАСПИСАНИЯ ОДНОЙ ГРУППЫ ──────────────────────────────────────────
def close_error_modal():
    """
    Закрывает ВСЕ открытые модальные окна ошибок.
    Возвращает True если хотя бы одно окно было найдено.
    Обрабатывает оба варианта:
      - "Ошибка загрузки расписания занятий"
      - "Произошла ошибка получения расписания занятий"
    """
    modals = driver.find_elements(By.CSS_SELECTOR, 'div.placeholder div.litebox')
    if not modals:
        return False
    found = False
    for modal in modals:
        try:
            if not modal.is_displayed():
                continue
            found = True
            try:
                text = modal.find_element(By.CSS_SELECTOR, 'p.big').text.strip()
                print(f"  ⚠️  {text}")
            except Exception:
                pass
            # Закрываем — сначала крестик, потом кнопка
            closed = False
            for selector in ('span.close', 'button'):
                try:
                    modal.find_element(By.CSS_SELECTOR, selector).click()
                    closed = True
                    break
                except Exception:
                    pass
            if not closed:
                # Крайний случай — JS click
                try:
                    driver.execute_script("arguments[0].click();",
                        modal.find_element(By.CSS_SELECTOR, 'button'))
                except Exception:
                    pass
            time.sleep(0.3)  # небольшая пауза чтобы модалка закрылась
        except Exception:
            pass
    return found

def parse_group_schedule(group_name, group_id):
    print(f"\n  Парсим {group_name} (id={group_id})...")
    driver.get(f"{BASE_URL}/schedule/#/timetable/studentGroup/{group_id}")

    # Ждём пока загрузится либо расписание, либо ошибка(и)
    try:
        WebDriverWait(driver, WAIT_FOR_WEEK).until(lambda d:
            d.find_elements(By.CSS_SELECTOR, 'div.week') or
            d.find_elements(By.CSS_SELECTOR, 'div.placeholder div.litebox')
        )
    except TimeoutException:
        print(f"  ❌ Страница не ответила, пропускаем")
        return []

    # Даём время — может появиться второе модальное окно
    time.sleep(0.5)

    # Закрываем все модалки. Если хоть одна была — пропускаем группу
    if close_error_modal():
        print(f"  ❌ Ошибка загрузки расписания, пропускаем {group_name}")
        return []

    # Расписание загрузилось — небольшая пауза чтобы Angular достроил все блоки
    time.sleep(2)

    lessons = []
    counter = 0

    try:
        for week in driver.find_elements(By.CSS_SELECTOR, 'div.week'):
            week_start = None
            try:
                week_start = parse_date_from_week_header(
                    week.find_element(By.TAG_NAME, 'h4').text
                )
            except Exception:
                pass

            for day_el in week.find_elements(By.CSS_SELECTOR, 'dl[x-ng-repeat="day in week"]'):
                day_short = ''
                try:
                    day_short = day_el.find_element(By.CSS_SELECTOR, 'dt big').text.strip()
                except Exception:
                    pass

                lesson_date = get_date_for_day(week_start, day_short) if week_start else ''

                for event in day_el.find_elements(By.CSS_SELECTOR, 'div.event'):
                    pair_number = start_time = end_time = ''
                    try:
                        pair_number = event.find_element(
                            By.CSS_SELECTOR, 'div.time div').text.strip()
                    except Exception:
                        pass
                    try:
                        raw   = event.find_element(
                            By.CSS_SELECTOR, 'div.time small').text.strip()
                        clean = raw.replace('–', '-').replace('—', '-')
                        if '-' in clean:
                            start_time, end_time = [p.strip() for p in clean.split('-', 1)]
                    except Exception:
                        pass

                    lesson_divs = event.find_elements(
                        By.CSS_SELECTOR, 'div.lessons div.lesson')
                    if not lesson_divs:
                        continue

                    for ld in lesson_divs:
                        subject = classroom = teacher = ''
                        try:
                            subject   = ld.find_element(
                                By.CSS_SELECTOR, 'span.subject').text.strip()
                        except Exception:
                            pass
                        try:
                            classroom = ld.find_element(
                                By.CSS_SELECTOR, 'small.classroom').text.strip().replace('ауд. ', '')
                        except Exception:
                            pass
                        try:
                            teacher   = ld.find_element(
                                By.CSS_SELECTOR, 'small.teacher').text.strip()
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
    except Exception as e:
        print(f"  Ошибка парсинга {group_name}: {e}")

    print(f"  → {len(lessons)} уроков")
    return lessons

# ── MAIN ──────────────────────────────────────────────────────────────────────
try:
    # Авторизация
    print("── Авторизация ──────────────────────────────────────────")
    driver.get(SCHEDULE_URL)
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.TAG_NAME, "body")))

    # Снимаем ng-hide с формы
    driver.execute_script("""
        var s = document.createElement('style');
        s.innerHTML = '.ng-hide { display: block !important; }';
        document.head.appendChild(s);
    """)
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.NAME, "formAuth")))
    time.sleep(2)

    form   = driver.find_element(By.NAME, "formAuth")
    inputs = form.find_elements(By.TAG_NAME, "input")
    login_field = password_field = None
    for inp in inputs:
        if inp.get_attribute("type") == "text"     or inp.get_attribute("name") == "login":
            login_field    = inp
        if inp.get_attribute("type") == "password" or inp.get_attribute("name") == "password":
            password_field = inp

    if not login_field or not password_field:
        raise Exception("Поля логина/пароля не найдены")

    driver.execute_script("arguments[0].value = arguments[1];", login_field,    LOGIN)
    driver.execute_script("arguments[0].value = arguments[1];", password_field, PASSWORD)

    try:
        driver.execute_script("arguments[0].click();",
            driver.find_element(By.CSS_SELECTOR,
                "form[name='formAuth'] button[type='submit']"))
    except Exception:
        driver.execute_script("arguments[0].submit();", form)

    # Ждём редиректа после логина
    WebDriverWait(driver, 15).until(lambda d: d.current_url != SCHEDULE_URL)
    # Ждём полной загрузки Angular после логина
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, 'hgroup, div.week, nav.navbar')))
    time.sleep(2)
    print(f"✅ Авторизован. URL: {driver.current_url}")

    # Обнаружение групп
    groups = discover_groups()

    if not groups:
        raise Exception("Не удалось найти ни одной группы")

    print(f"\n✅ Найдено групп: {len(groups)}")
    for name, gid in sorted(groups.items()):
        print(f"   {name}: {gid}")

    # Парсинг расписания
    print("\n── Парсинг расписания ───────────────────────────────────")
    schedule_data = {}
    for group_name, group_id in sorted(groups.items()):
        schedule_data[group_name] = parse_group_schedule(group_name, group_id)
        time.sleep(BETWEEN_GROUPS)

    # Сохранение
    with open('schedule.json', 'w', encoding='utf-8') as f:
        json.dump(schedule_data, f, ensure_ascii=False, indent=4)

    total = sum(len(v) for v in schedule_data.values())
    print(f"\n✅ Готово! {total} уроков, {len(schedule_data)} групп → schedule.json")

except Exception as e:
    import traceback
    print(f"\n❌ Ошибка: {e}")
    traceback.print_exc()

finally:
    driver.quit()
