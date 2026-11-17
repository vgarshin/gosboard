"""Optional local-demo smoke test: install Playwright in the test venv, then run this file."""
import json
import os
import re
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path='/usr/bin/google-chrome', headless=True,
        args=['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'])
    base_url = os.getenv('TEST_BASE_URL', 'http://localhost:5173')
    context = browser.new_context(base_url=base_url, viewport={'width': 1440, 'height': 1000}, permissions=['microphone'])
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('console', lambda message: errors.append(message.text) if message.type == 'error' and 'Content Security Policy' in message.text else None)
    page.goto(base_url)
    assert page.request.get('/api/bootstrap').json()['demo'], 'Run this test only against demo mode'
    page.get_by_role('button', name='Открыть локальное демо').click()
    page.locator('button.new-chat').wait_for()
    page.locator('button.new-chat').click()
    page.get_by_role('heading', name='О чём поговорим?').wait_for()
    assert page.locator('header .model-pill').count()==0
    assert page.get_by_role('combobox',name='Модель',exact=True).count()==0
    page.get_by_role('button',name='Конфигурация',exact=True).click()
    page.get_by_role('combobox',name='Модель разговора').select_option('alice-flash')
    page.get_by_role('combobox',name='Инструкция разговора').select_option('peter-council')
    page.get_by_role('button',name='Сохранить настройки разговора').click()
    expect(page.get_by_role('status')).to_have_text('Сохранено')
    page.get_by_role('button',name='Закрыть конфигурацию').click()
    expect(page.get_by_role('switch', name='RAG', exact=True)).to_be_disabled()
    internet=page.get_by_role('switch', name='Поиск в интернете', exact=True)
    if page.request.get('/api/config').json()['capabilities']['web_search']:
        expect(internet).to_be_enabled()
    else:
        expect(internet).to_be_disabled()
    page.screenshot(path='/tmp/gosboard-desktop.png', full_page=True)
    page.get_by_role('button', name='ПОПРОБУЙТЕ ДЛЯ НАЧАЛА', exact=False).click()
    assert 'Привет. Представь' in page.get_by_role('textbox', name='Сообщение').input_value()
    page.get_by_role('button', name='Отправить сообщение', exact=True).click()
    page.get_by_role('button', name='Озвучить', exact=True).first.wait_for()
    page.get_by_role('button', name='Озвучить', exact=True).first.click()
    page.wait_for_timeout(1000)
    page.reload()
    page.get_by_text('Дворянин. Новые школы', exact=False).wait_for()
    page.get_by_role('button',name='Конфигурация',exact=True).click()
    expect(page.get_by_role('combobox',name='Модель разговора')).to_have_value('alice-flash')
    expect(page.get_by_role('combobox',name='Инструкция разговора')).to_have_value('peter-council')
    page.get_by_role('button',name='Закрыть конфигурацию').click()
    page.locator('button.new-chat').click()
    page.get_by_role('heading', name='О чём поговорим?').wait_for()
    page.get_by_role('button', name='Записать голос', exact=True).click()
    page.get_by_role('button', name='Закончить запись', exact=True).wait_for()
    page.wait_for_timeout(500)
    page.get_by_role('button', name='Закончить запись', exact=True).click()
    expect(page.get_by_role('textbox', name='Сообщение')).to_have_value(re.compile('Расскажи.*'))
    page.get_by_role('button', name='Конфигурация', exact=True).click()
    page.get_by_role('heading', name='Конфигурация', exact=True).wait_for()
    page.get_by_role('button', name='Закрыть конфигурацию', exact=True).click()
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path='/tmp/gosboard-mobile.png', full_page=True)
    page.get_by_role('button', name='Открыть меню', exact=True).click()
    page.get_by_role('navigation', name='История разговоров').wait_for()
    assert errors == [], errors
    print(json.dumps({'browser_errors':errors,'passed':['login','demo preset','chat generation','TTS transport','history after reload','new chat','microphone capture and STT','admin config','mobile layout and navigation','persistent model/prompt selection','unavailable retrieval switches disabled']}, ensure_ascii=False))
    browser.close()
