@echo off
echo ========================================
echo    Парсинг расписания ЧРТ
echo ========================================

:: Переходим в папку скрипта
cd /d "%~dp0"

:: Запускаем парсер
echo [1/3] Запуск парсера...
python login_script.py
if %errorlevel% neq 0 (
    echo ОШИБКА: парсинг завершился с ошибкой
    pause
    exit /b 1
)
echo Парсинг завершён успешно.

:: Пушим на GitHub
echo [2/3] Отправка на GitHub...
git add schedule.json
git diff --staged --quiet
if %errorlevel% neq 0 (
    git commit -m "Автообновление расписания %date% %time%"
    git push
    echo Расписание отправлено на GitHub.
) else (
    echo Расписание не изменилось, пуш не нужен.
)

echo [3/3] Готово!
