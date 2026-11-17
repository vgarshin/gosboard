"""Run against the isolated slow-token demo preview; never against production."""
import json
import os
from uuid import uuid4
from playwright.sync_api import sync_playwright,expect

BASE_URL=os.getenv('TEST_BASE_URL','http://localhost:5194')

with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1000})
    page.add_init_script("window.audioStarts=0;const original=AudioBufferSourceNode.prototype.start;AudioBufferSourceNode.prototype.start=function(...args){window.audioStarts++;return original.apply(this,args)}")
    failures=[];page.on('pageerror',lambda e:failures.append(str(e)))
    page.goto(BASE_URL);assert page.request.get(BASE_URL+'/api/bootstrap').json()['demo']
    page.get_by_role('button',name='Открыть локальное демо').click()
    page.locator('button.new-chat').click()
    mode=page.get_by_role('combobox',name='Режим общения',exact=True)
    expect(mode).to_have_value('text')
    mode.select_option('voice')
    page.get_by_role('textbox',name='Сообщение').fill('Пример потокового ответа '+uuid4().hex)
    page.get_by_role('button',name='Отправить сообщение',exact=True).click()
    preview=page.get_by_label('Потоковый ответ',exact=True).filter(has=page.locator('article'))
    expect(page.locator('.stream-preview .assistant .message-text')).to_contain_text('Дворянин')
    # Early text is visible while generation is still in flight.
    expect(page.get_by_role('button',name='Остановить ответ',exact=True)).to_be_visible()
    page.wait_for_function('() => window.audioStarts > 0')
    expect(page.locator('.stream-preview')).to_be_visible()
    page.get_by_role('button',name='Остановить звук',exact=True).click()
    expect(page.get_by_role('button',name='Остановить звук',exact=True)).to_have_count(0)
    expect(page.locator('.stream-preview')).to_have_count(0,timeout=15000)
    expect(page.get_by_role('button',name='Озвучить',exact=True)).to_have_count(1)
    page.reload();expect(mode).to_have_value('voice')
    page.get_by_role('textbox',name='Сообщение').fill('Остановить второй ответ')
    page.get_by_role('button',name='Отправить сообщение',exact=True).click()
    expect(page.locator('.stream-preview .assistant .message-text')).to_contain_text('Дворянин')
    page.get_by_role('button',name='Остановить ответ',exact=True).click()
    expect(page.locator('.stream-preview')).to_have_count(0)
    expect(page.get_by_role('textbox',name='Сообщение')).to_have_value('Остановить второй ответ')
    expect(page.get_by_role('button',name='Озвучить',exact=True)).to_have_count(1)
    page.get_by_role('button',name='Закрыть ошибку').click()
    mode.select_option('text')
    page.get_by_text('Дополнительные настройки',exact=True).click()
    page.get_by_role('switch',name='Классический ответ',exact=True).check()
    page.get_by_role('textbox',name='Сообщение').fill('Классический ответ')
    page.get_by_role('button',name='Отправить сообщение',exact=True).click()
    expect(page.get_by_role('button',name='Озвучить',exact=True)).to_have_count(2,timeout=15000)
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path='/tmp/gosboard-streaming-mobile.png',full_page=True)
    assert failures==[],failures
    print(json.dumps({'passed':['early text','stop sound independently','persistent controls','cancel without partial history','classic mode','mobile layout'],'browser_errors':failures}))
    browser.close()
