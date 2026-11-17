"""Local demo browser rehearsal for the live web switch and mixed citations."""
import json,os,time
from playwright.sync_api import sync_playwright,expect
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True)
 context=browser.new_context(base_url=os.getenv('TEST_BASE_URL','http://localhost:5182'),viewport={'width':1440,'height':1000})
 page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto('/');assert page.request.get('/api/bootstrap').json()['demo']
 page.get_by_role('button',name='Открыть локальное демо').click();page.locator('button.new-chat').click()
 web=page.get_by_role('switch',name='Поиск в интернете',exact=True)
 expect(web).to_be_enabled();web.check();expect(web).to_be_enabled()
 question='Что изучали в школе навигации при Петре Первом?'
 page.get_by_role('textbox',name='Сообщение').fill(question);page.get_by_role('button',name='Отправить сообщение',exact=True).click()
 marker=page.locator('a.citation-marker').filter(has_text='[W1]').first
 expect(marker).to_be_visible(timeout=15000)
 count=len(context.pages);marker.click()
 modal=page.get_by_role('dialog',name='Источник ответа')
 expect(modal.get_by_text('Показан фрагмент поисковой выдачи. Полная страница не загружалась.',exact=True)).to_be_visible()
 expect(modal.locator('mark.source-excerpt')).to_be_visible()
 external=modal.get_by_role('link',name='Открыть страницу',exact=True)
 assert external.get_attribute('href').startswith('https://') and external.get_attribute('target')=='_blank'
 assert len(context.pages)==count
 page.screenshot(path='/tmp/gosboard-web-source.png',full_page=True)
 page.keyboard.press('Escape');expect(modal).not_to_be_visible();expect(marker).to_be_focused()
 page.get_by_role('button',name='Базы знаний',exact=True).click()
 name='Совместный поиск '+str(int(time.time()))
 page.get_by_role('textbox',name='Название новой базы').fill(name);page.get_by_role('button',name='Создать',exact=True).click()
 expect(page.get_by_role('combobox',name='Управляемая база знаний')).not_to_have_value('')
 text=('В школе навигации при Петре Первом изучали математику и навигацию. Образовательная реформа готовила специалистов для флота и государственной службы. '*5).encode()
 page.get_by_label('Документ для базы знаний').set_input_files({'name':'school.txt','mimeType':'text/plain','buffer':text})
 page.get_by_role('button',name='Загрузить документ').click();expect(page.get_by_text('Готов',exact=True)).to_be_visible(timeout=20000)
 page.get_by_role('button',name='Закрыть базы знаний').click()
 page.get_by_role('combobox',name='База знаний',exact=True).select_option(label=name)
 rag=page.get_by_role('switch',name='RAG',exact=True);rag.check();expect(rag).to_be_enabled()
 page.get_by_role('textbox',name='Сообщение').fill(question);page.get_by_role('button',name='Отправить сообщение',exact=True).click()
 last=page.locator('article.message.assistant').last
 expect(last.locator('a.citation-marker').filter(has_text='[D1]')).to_be_visible(timeout=15000)
 expect(last.locator('a.citation-marker').filter(has_text='[W1]')).to_be_visible()
 page.screenshot(path='/tmp/gosboard-combined-search.png',full_page=True)
 page.reload();expect(web).to_be_checked();expect(rag).to_be_checked()
 page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
 page.locator('a.citation-marker').filter(has_text='[W1]').last.click();expect(modal.locator('mark.source-excerpt')).to_be_visible()
 page.screenshot(path='/tmp/gosboard-web-mobile.png',full_page=True)
 page.get_by_role('button',name='Закрыть источник').click()
 web.uncheck();expect(web).to_be_enabled();rag.uncheck();expect(rag).to_be_enabled()
 page.get_by_role('textbox',name='Сообщение').fill('Продолжим обычный разговор');page.get_by_role('button',name='Отправить сообщение',exact=True).click()
 expect(page.locator('article.message.assistant')).to_have_count(3)
 assert page.locator('article.message.assistant').last.locator('a.citation-marker').count()==0
 assert errors==[],errors
 print(json.dumps({'internet_switch':'enabled','web_answer':'cited','web_source_popup':'passed','combined_RAG_web':'passed','persistent_switches':'passed','both_off':'passed','mobile':'passed','browser_errors':errors}))
 browser.close()
