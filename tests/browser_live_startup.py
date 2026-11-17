"""Pausing while AudioWorklet startup is delayed must release acquired microphone tracks."""
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from browser_live_fixture import install_microphone
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True,args=['--autoplay-policy=no-user-gesture-required'])
 page=browser.new_page();install_microphone(page);held=[];paid=[]
 page.route('**/live-pcm-worklet.js',lambda route:held.append(route))
 page.on('request',lambda r:paid.append(r.url) if r.method=='POST' and any(x in r.url for x in ('/stt','/messages/stream')) else None)
 page.goto('http://localhost:5196');assert page.request.get('http://localhost:5196/api/bootstrap').json()['demo']
 page.get_by_role('button',name='Открыть локальное демо').click();page.locator('button.new-chat').click()
 page.get_by_role('combobox',name='Режим общения',exact=True).select_option('live')
 page.get_by_role('button',name='Начать живой диалог',exact=True).click()
 page.wait_for_function('() => liveTest.active===1')
 page.get_by_role('button',name='Пауза',exact=True).click()
 page.wait_for_function('() => liveTest.active===0')
 for route in held:
  try:route.fulfill(content_type='application/javascript',body=(Path(__file__).parents[1]/'frontend/public/live-pcm-worklet.js').read_text())
  except Exception:pass
 expect(page.get_by_role('button',name='Продолжить',exact=True)).to_be_visible()
 page.wait_for_timeout(400);assert page.evaluate('liveTest.active')==0 and not paid
 print('Delayed-worklet pause releases microphone before startup resolves; no paid requests.')
 browser.close()
