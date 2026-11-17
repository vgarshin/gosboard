"""Local demo browser rehearsal for shared RAG; uses no paid services."""
import json
import os
import time
from playwright.sync_api import sync_playwright, expect

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path='/usr/bin/google-chrome', headless=True)
    context = browser.new_context(base_url=os.getenv('TEST_BASE_URL', 'http://localhost:5175'), viewport={'width':1440,'height':1000})
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('/')
    assert page.request.get('/api/bootstrap').json()['demo'], 'Only run against local demo'
    page.get_by_role('button', name='Открыть локальное демо').click()
    page.get_by_role('button', name='Базы знаний', exact=True).click()
    name = 'Проверка RAG ' + str(int(time.time()))
    page.get_by_role('textbox', name='Название новой базы').fill(name)
    page.get_by_role('button', name='Создать', exact=True).click()
    expect(page.get_by_role('combobox', name='Управляемая база знаний')).not_to_have_value('')
    text = ('Образовательная реформа Петра Первого. Дворянские дети изучали математику, навигацию и иностранные языки. Школы готовили специалистов для армии, флота и государственной службы. ' * 5).encode()
    page.get_by_label('Документ для базы знаний').set_input_files({'name':'reform.txt','mimeType':'text/plain','buffer':text})
    page.get_by_role('button', name='Загрузить документ').click()
    expect(page.get_by_text('Готов', exact=True)).to_be_visible(timeout=20000)
    page.get_by_role('button', name='Закрыть базы знаний').click()
    page.locator('button.new-chat').click()
    page.get_by_role('combobox', name='База знаний', exact=True).select_option(label=name)
    page.get_by_role('switch', name='RAG', exact=True).check()
    page.get_by_role('textbox', name='Сообщение').fill('Что изучали дворянские дети при Петре Первом?')
    page.get_by_role('button', name='Отправить сообщение', exact=True).click()
    citation = page.locator('a.citation-marker').first
    expect(citation).to_be_visible(timeout=20000)
    page.screenshot(path='/tmp/gosboard-rag-chat.png', full_page=True)
    original_pages = len(context.pages)
    citation.click()
    modal = page.get_by_role('dialog', name='Источник ответа')
    expect(modal.locator('mark.source-excerpt')).to_contain_text('Дворянские дети')
    assert len(context.pages) == original_pages
    original = modal.get_by_role('link', name='Скачать оригинал', exact=False).get_attribute('href')
    assert page.request.get(original).body() == text
    page.screenshot(path='/tmp/gosboard-rag-source.png', full_page=True)
    # Keyboard close returns focus to the citation and preserves the current chat.
    page.keyboard.press('Escape')
    expect(modal).not_to_be_visible()
    expect(citation).to_be_focused()
    assert page.locator('form.composer').get_by_role('switch', name='RAG', exact=True).count() == 1
    assert page.locator('form.composer').get_by_role('combobox', name='База знаний', exact=True).count() == 1
    page.reload()
    expect(page.get_by_role('switch', name='RAG', exact=True)).to_be_checked()
    page.get_by_role('switch', name='RAG', exact=True).uncheck()
    page.get_by_role('textbox', name='Сообщение').fill('Продолжим обычный разговор')
    page.get_by_role('button', name='Отправить сообщение', exact=True).click()
    expect(page.locator('article.message.assistant')).to_have_count(2)
    assert page.locator('article.message.assistant').last.locator('a.citation-marker').count() == 0
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.get_by_role('button', name='Базы знаний', exact=True).click()
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path='/tmp/gosboard-rag-mobile.png', full_page=True)
    assert errors == [], errors
    print(json.dumps({'browser_errors':errors,'passed':['admin create/upload','background indexing','select shared base','enable RAG','cited answer','source popup without new tab','authenticated original','settings persist','RAG off','mobile layout']},ensure_ascii=False))
    browser.close()
