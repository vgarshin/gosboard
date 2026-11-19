"""Optional browser check against an isolated local demo on port 5195."""
import json
import os
from playwright.sync_api import sync_playwright,expect

BASE_URL=os.getenv('TEST_BASE_URL','http://localhost:5195')

with sync_playwright() as p:
 browser=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True)
 page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];paid=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.on('request',lambda r:paid.append(r.url) if r.method=='POST' and any(x in r.url for x in ('/messages','/stt','/speech')) else None)
 page.goto(BASE_URL);assert page.request.get(BASE_URL+'/api/bootstrap').json()['demo']
 page.get_by_role('button',name='Открыть локальное демо').click()
 page.get_by_role('button',name='Основное пространство').click()
 draft=page.get_by_role('textbox',name='Сообщение');expect(draft).to_be_enabled();draft.fill('Черновик остаётся в чате')
 help=page.get_by_role('button',name='Руководство пользователя',exact=True);help.click()
 dialog=page.get_by_role('dialog',name='Руководство пользователя',exact=True)
 expect(dialog).to_be_visible()
 close=dialog.get_by_role('button',name='Закрыть руководство',exact=True);expect(close).to_be_focused()
 for name in ('Как пользоваться чатом','Интернет и RAG','Голосовой режим','Живой диалог','Параметры и доступ','Если что-то не работает'):
  expect(dialog.get_by_role('tab',name=name,exact=True)).to_have_count(1)
 voice=dialog.get_by_role('tab',name='Голосовой режим',exact=True)
 voice.click()
 expect(dialog.get_by_role('heading',name='Режим общения и голос',exact=True)).to_be_visible()
 expect(voice).to_have_attribute('aria-selected','true')
 page.keyboard.press('ArrowRight')
 expect(dialog.get_by_role('tab',name='Живой диалог',exact=True)).to_be_focused()
 page.keyboard.press('Shift+Tab');expect(close).to_be_focused()
 page.keyboard.press('Escape');expect(dialog).to_have_count(0);expect(help).to_be_focused();expect(draft).to_have_value('Черновик остаётся в чате')
 assert page.evaluate('location.hash')==''
 page.set_viewport_size({'width':390,'height':844});help.click();expect(dialog).to_be_visible()
 for link in ('Интернет и RAG','Голосовой режим','Параметры и доступ','Если что-то не работает'):
  dialog.get_by_role('tab',name=link,exact=True).click()
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
 dialog.get_by_role('tab',name='Живой диалог',exact=True).click()
 scroll=dialog.locator('.manual-scroll')
 assert scroll.evaluate('el => el.scrollHeight > el.clientHeight && parseFloat(getComputedStyle(el).paddingLeft) >= 18')
 scroll.evaluate('el => el.scrollTop = 100')
 assert scroll.evaluate('el => el.scrollTop') > 0
 assert page.evaluate('document.querySelector(".manual-modal").getBoundingClientRect().bottom <= innerHeight')
 # Close remains visible while the independent manual body scrolls.
 expect(close).to_be_in_viewport()
 page.screenshot(path='/tmp/gosboard-user-manual-mobile.png',full_page=True)
 close.click();expect(draft).to_have_value('Черновик остаётся в чате')
 assert not errors,errors
 assert not paid,paid
 print(json.dumps({'passed':['manual sections','keyboard tabs','focus trap/return','Escape and close','draft preserved','mobile scrolling/layout','no paid calls'],'browser_errors':errors}))
 browser.close()
