"""Live-dialog failure/lifecycle checks with no paid providers."""
import json
from playwright.sync_api import sync_playwright,expect
from browser_live_fixture import install_microphone

with sync_playwright() as p:
 browser=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True,args=['--autoplay-policy=no-user-gesture-required'])
 context=browser.new_context();page=context.new_page();install_microphone(page)
 page.clock.install();errors=[];sent=[];recognized=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.on('request',lambda r:sent.append(r.url) if r.method=='POST' and r.url.endswith('/messages/stream') else None)
 page.goto('http://localhost:5196');assert page.request.get('http://localhost:5196/api/bootstrap').json()['demo']
 page.get_by_role('button',name='Открыть локальное демо').click();page.locator('button.new-chat').click()
 mode=page.get_by_role('combobox',name='Режим общения',exact=True);mode.select_option('live')
 panel=page.get_by_role('region',name='Управление живым диалогом')
 draft=page.get_by_role('textbox',name='Сообщение')
 def recognize(route):recognized.append(len(route.request.post_data_buffer or b''));route.fulfill(json={'text':'   '})
 page.route('**/api/stt',recognize)
 page.get_by_role('button',name='Начать живой диалог',exact=True).click()
 expect(page.get_by_role('button',name='Продолжить',exact=True)).to_be_visible(timeout=10000)
 expect(panel).to_contain_text('Речь не распознана')
 assert len(recognized)==1 and not sent and page.evaluate('liveTest.active')==0
 # Idle listening has no recognition calls and is bounded to one minute.
 page.evaluate("liveTest.input='silence'")
 page.get_by_role('button',name='Продолжить',exact=True).click()
 expect(panel).to_contain_text('Слушаю — задайте вопрос')
 page.clock.fast_forward(61000)
 expect(panel).to_contain_text('Минуту не было речи')
 assert len(recognized)==1 and not sent and page.evaluate('liveTest.active')==0
 # A delayed transcript arriving after Pause must never become a question.
 page.unroute('**/api/stt');held=[];page.route('**/api/stt',lambda route:held.append(route))
 page.evaluate("liveTest.input='speech'")
 page.get_by_role('button',name='Продолжить',exact=True).click()
 expect(panel).to_contain_text('Распознаю речь',timeout=10000)
 page.get_by_role('button',name='Пауза',exact=True).click()
 for route in held:
  try:route.fulfill(json={'text':'LATE TRANSCRIPT MUST NOT SEND'})
  except Exception:pass
 expect(draft).to_be_enabled();expect(draft).to_have_value('');assert not sent
 # Budget/service failure pauses rather than retrying automatically.
 page.unroute('**/api/stt');page.route('**/api/stt',lambda r:r.fulfill(json={'text':'Вопрос с ошибкой бюджета'}))
 page.route('**/api/chats/*/messages/stream',lambda r:r.fulfill(status=200,content_type='text/event-stream',body='data: '+json.dumps({'event':'error','detail':'Достигнут лимит расходов.'},ensure_ascii=False)+'\n\n'))
 page.get_by_role('button',name='Продолжить',exact=True).click()
 expect(panel).to_contain_text('Достигнут лимит расходов',timeout=10000)
 assert page.evaluate('liveTest.active')==0 and len(sent)==1
 expect(draft).to_have_value('Вопрос с ошибкой бюджета')
 page.get_by_role('button',name='Продолжить',exact=True).click()
 expect(page.get_by_role('alert')).to_contain_text('Отправьте или очистите черновик')
 assert len(sent)==1
 page.unroute('**/api/chats/*/messages/stream');draft.fill('')
 # Continuous speech must stop at the 24-second cap and remain for review.
 page.evaluate("liveTest.input='continuous'")
 page.get_by_role('button',name='Продолжить',exact=True).click()
 expect(panel).to_contain_text('Достигнут предел записи',timeout=32000)
 expect(draft).to_have_value('Вопрос с ошибкой бюджета');expect(draft).to_be_enabled()
 assert len(sent)==1 and page.evaluate('liveTest.active')==0
 # Stop is always available while paused; reconnect does not start microphone.
 page.get_by_role('button',name='Завершить диалог',exact=True).click()
 expect(page.get_by_role('button',name='Начать живой диалог',exact=True)).to_be_visible()
 assert not errors,errors
 print(json.dumps({'passed':['empty STT pauses','idle timeout without paid calls','late transcript discarded','budget error pauses without retry','draft review required before resume','duration cap review without LLM','explicit stop'],'browser_errors':errors}))
 browser.close()
