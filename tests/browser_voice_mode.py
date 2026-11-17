"""Isolated demo browser checks with a fake microphone; no paid providers."""
import json
import os
from uuid import uuid4
from playwright.sync_api import sync_playwright,expect

BASE_URL=os.getenv('TEST_BASE_URL','http://localhost:5194')

with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True,args=['--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream'])
    page=browser.new_page(viewport={'width':1440,'height':1000},permissions=['microphone'])
    page.add_init_script('window.audioStarts=0;const orig=AudioBufferSourceNode.prototype.start;AudioBufferSourceNode.prototype.start=function(...args){window.audioStarts++;return orig.apply(this,args)}')
    errors=[];requests=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('request',lambda r:requests.append(r.url) if r.method=='POST' else None)
    page.goto(BASE_URL);assert page.request.get(BASE_URL+'/api/bootstrap').json()['demo']
    page.get_by_role('button',name='Открыть локальное демо').click()
    page.locator('button.new-chat').click()
    mode=page.get_by_role('combobox',name='Режим общения',exact=True)
    expect(mode).to_have_value('text');mode.select_option('voice')
    draft=page.get_by_role('textbox',name='Сообщение');expect(draft).to_be_enabled()
    draft.fill('Черновик '+uuid4().hex[:8])
    def record():
        page.get_by_role('button',name='Записать голос',exact=True).click()
        page.get_by_role('button',name='Закончить запись',exact=True).wait_for()
        page.wait_for_timeout(350)
    def sends():return sum(url.endswith('/messages/stream') or url.endswith('/messages') for url in requests)
    def stt():return sum(url.endswith('/stt') for url in requests)
    record();page.get_by_role('button',name='Закончить запись',exact=True).click()
    expect(page.get_by_role('button',name='Озвучить',exact=True)).to_have_count(1,timeout=15000)
    page.wait_for_function('() => window.audioStarts>0')
    assert sends()==1 and stt()==1
    expect(draft).to_have_value('')
    # Cancel must not transcribe, send, or erase an existing draft.
    draft.fill('Сохранить этот черновик');record()
    page.get_by_role('button',name='Отменить запись',exact=True).click()
    expect(draft).to_be_enabled();expect(draft).to_have_value('Сохранить этот черновик')
    assert sends()==1 and stt()==1
    page.get_by_text('Дополнительные настройки',exact=True).click()
    review=page.get_by_role('switch',name='Проверять текст перед отправкой',exact=True);review.check()
    expect(draft).to_be_enabled();record();page.get_by_role('button',name='Закончить запись',exact=True).click()
    page.wait_for_function('() => document.querySelector("textarea").value.includes("Расскажи")')
    expect(draft).to_be_enabled();assert sends()==1 and stt()==2
    page.get_by_role('button',name='Отправить сообщение',exact=True).click()
    expect(page.get_by_role('button',name='Озвучить',exact=True)).to_have_count(2,timeout=15000)
    page.reload();expect(mode).to_have_value('voice')
    page.get_by_text('Дополнительные настройки',exact=True).click();expect(review).to_be_checked();review.uncheck()
    expect(draft).to_be_enabled()
    # Empty/failed recognition never becomes an LLM request.
    page.route('**/api/stt',lambda route:route.fulfill(json={'text':'   '}))
    record();page.get_by_role('button',name='Закончить запись',exact=True).click()
    expect(page.get_by_role('alert')).to_contain_text('Речь не распознана')
    expect(draft).to_be_enabled();assert sends()==2
    page.unroute('**/api/stt');page.get_by_role('button',name='Закрыть ошибку').click()
    page.route('**/api/stt',lambda route:route.fulfill(status=502,json={'detail':'Тестовая ошибка распознавания'}))
    record();page.get_by_role('button',name='Закончить запись',exact=True).click()
    expect(page.get_by_role('alert')).to_contain_text('Тестовая ошибка распознавания')
    expect(draft).to_be_enabled();assert sends()==2
    page.unroute('**/api/stt');page.get_by_role('button',name='Закрыть ошибку').click()
    # A time-limited recording is reviewed, even in automatic voice mode.
    page.clock.install();record();page.clock.fast_forward(24000)
    page.wait_for_function('() => document.querySelector("textarea").value.includes("Расскажи")')
    expect(draft).to_be_enabled();expect(page.get_by_role('alert')).to_contain_text('Достигнут предел записи')
    assert sends()==2
    mode.select_option('text');expect(draft).to_be_enabled()
    expect(page.get_by_role('switch',name='Проверять текст перед отправкой',exact=True)).to_be_disabled()
    # Legacy remembered audio preference must not silently turn text mode into voice.
    me=page.request.get(BASE_URL+'/api/me').json()
    legacy=page.request.post(BASE_URL+'/api/chats',headers={'Origin':BASE_URL,'X-CSRF-Token':me['csrf']},data={'streaming_enabled':False,'auto_speech':True}).json()
    page.reload();expect(mode).to_have_value('text')
    page.get_by_text('Дополнительные настройки',exact=True).click()
    classic=page.get_by_role('switch',name='Классический ответ',exact=True)
    expect(classic).to_be_checked();classic.uncheck();expect(draft).to_be_enabled();expect(mode).to_have_value('text')
    saved=next(chat['settings'] for chat in page.request.get(BASE_URL+'/api/chats').json() if chat['id']==legacy['id'])
    assert saved['communication_mode']=='text' and saved['streaming_enabled'] and not saved['auto_speech']
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.screenshot(path='/tmp/gosboard-voice-mode-mobile.png',full_page=True)
    assert errors==[],errors
    print(json.dumps({'passed':['voice automatic send and audio','preserved draft','cancel without STT/send','optional review','persisted mode/review','empty/failed STT blocked','recording limit requires review','text mode','mobile'],'browser_errors':errors}))
    browser.close()
