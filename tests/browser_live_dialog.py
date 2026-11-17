"""Full local-demo live loop with real WebRTC VAD and deterministic microphone speech."""
import json
from playwright.sync_api import sync_playwright,expect
from browser_live_fixture import install_microphone

with sync_playwright() as p:
 browser=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True,args=['--autoplay-policy=no-user-gesture-required'])
 context=browser.new_context(viewport={'width':1440,'height':1000})
 page=context.new_page();install_microphone(page)
 errors=[];stt=[];sends=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.on('request',lambda r:sends.append(r.url) if r.method=='POST' and r.url.endswith('/messages/stream') else None)
 def recognize(route):
  stt.append(len(route.request.post_data_buffer or b''));route.fulfill(json={'text':'Вопрос живого диалога '+str(len(stt))})
 page.route('**/api/stt',recognize)
 page.goto('http://localhost:5196');assert page.request.get('http://localhost:5196/api/bootstrap').json()['demo']
 page.get_by_role('button',name='Открыть локальное демо').click();page.locator('button.new-chat').click()
 mode=page.get_by_role('combobox',name='Режим общения',exact=True);mode.select_option('live')
 panel=page.get_by_role('region',name='Управление живым диалогом')
 expect(panel.get_by_text('Микрофон выключен',exact=True)).to_be_visible()
 assert page.evaluate('liveTest.opens')==0
 page.get_by_text('Дополнительные настройки',exact=True).click()
 gap=page.get_by_role('combobox',name='Пауза перед отправкой',exact=True);gap.select_option('long')
 expect(page.get_by_role('textbox',name='Сообщение')).to_be_enabled()
 expect(page.get_by_role('switch',name='Классический ответ',exact=True)).to_be_disabled()
 page.get_by_role('button',name='Начать живой диалог',exact=True).click()
 expect(panel.get_by_text('Слушаю — задайте вопрос',exact=True)).to_be_visible(timeout=10000)
 # No manual Stop or Send: the detector must trigger both turns and wait for playback.
 page.wait_for_function('() => liveTest.audioStarts>0',timeout=15000)
 assert len(stt)==1 and len(sends)==1 and page.evaluate('liveTest.active')==0
 page.wait_for_function('() => liveTest.opens>=2',timeout=20000)
 page.wait_for_function('() => document.querySelectorAll(".message.assistant:not(.stream-preview .message)").length>=2',timeout=20000)
 page.get_by_role('button',name='Пауза',exact=True).click()
 expect(panel.get_by_text('Микрофон выключен',exact=True)).to_be_visible()
 assert len(stt)==2 and len(sends)==2 and page.evaluate('liveTest.active')==0
 assert page.evaluate('liveTest.overlaps')==0
 # Reload remembers mode/pause tolerance, but never restarts the microphone.
 page.reload();expect(mode).to_have_value('live')
 expect(page.get_by_role('button',name='Начать живой диалог',exact=True)).to_be_visible()
 assert page.evaluate('liveTest.opens')==0
 page.get_by_text('Дополнительные настройки',exact=True).click();expect(gap).to_have_value('long')
 # Silence starts no paid operation, and mode switching releases its microphone.
 page.evaluate("liveTest.input='silence'")
 before=len(stt);page.get_by_role('button',name='Начать живой диалог',exact=True).click()
 expect(panel.get_by_text('Слушаю — задайте вопрос',exact=True)).to_be_visible()
 page.wait_for_timeout(1800);assert len(stt)==before
 mode.select_option('text');expect(panel).to_have_count(0)
 assert page.evaluate('liveTest.active')==0
 # Late permission resolution cannot revive a stopped session.
 mode.select_option('live');expect(page.get_by_role('textbox',name='Сообщение')).to_be_enabled()
 page.evaluate('liveTest.delay=700')
 page.get_by_role('button',name='Начать живой диалог',exact=True).click()
 page.get_by_role('button',name='Завершить диалог',exact=True).click()
 page.wait_for_timeout(1100);assert page.evaluate('liveTest.active')==0
 assert len(stt)==before
 # Hiding a tab pauses listening; returning does not resume without a click.
 page.evaluate('liveTest.delay=0')
 page.get_by_role('button',name='Начать живой диалог',exact=True).click()
 expect(panel.get_by_text('Слушаю — задайте вопрос',exact=True)).to_be_visible()
 page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'))")
 expect(page.get_by_role('button',name='Продолжить',exact=True)).to_be_visible()
 assert page.evaluate('liveTest.active')==0
 page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:false})")
 # Denied mic stays paused with no retry loop.
 page.evaluate('liveTest.denied=true')
 page.get_by_role('button',name='Продолжить',exact=True).click()
 expect(page.get_by_role('alert')).to_contain_text('Разрешите доступ')
 assert page.evaluate('liveTest.active')==0 and len(stt)==before
 page.set_viewport_size({'width':390,'height':844})
 assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
 page.screenshot(path='/tmp/gosboard-live-dialog-mobile.png',full_page=True)
 assert not errors,errors
 print(json.dumps({'passed':['two automatic speech turns','real VAD','pause tolerance persisted','no microphone during playback','silence no STT','pause/stop release','no automatic start after reload','mode switch release','late permission cleanup','hidden tab pause','permission denial','mobile'],'browser_errors':errors}))
 browser.close()
