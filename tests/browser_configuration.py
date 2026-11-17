"""Local live-configuration UI rehearsal; restores the original config after checking hot updates."""
import os,json
from playwright.sync_api import sync_playwright,expect
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True)
    context=browser.new_context(base_url=os.getenv('TEST_BASE_URL','http://localhost:5179'),viewport={'width':1440,'height':1000})
    page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('/');assert page.request.get('/api/bootstrap').json()['demo']
    page.get_by_role('button',name='Открыть локальное демо').click()
    page.get_by_role('button',name='Конфигурация',exact=True).click()
    page.get_by_role('button',name='Общие настройки',exact=True).click()
    original=page.request.get('/api/editor/config').json()
    page.get_by_role('spinbutton',name='Температура',exact=True).fill('0.35')
    page.get_by_role('combobox',name='Голос по умолчанию').select_option('filipp')
    with page.expect_response(lambda r:r.url.endswith('/api/editor/config') and r.request.method=='PUT') as saved:
        page.get_by_role('button',name='Сохранить общую конфигурацию').click()
    assert saved.value.status==200
    expect(page.get_by_role('status')).to_have_text('Сохранено')
    updated=page.request.get('/api/config').json()
    assert updated['voice']=='filipp'
    assert page.locator('header .model-pill').count()==0
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path='/tmp/gosboard-live-configuration-mobile.png',full_page=True)
    page.get_by_role('button',name='Закрыть конфигурацию').click();page.reload()
    page.get_by_role('button',name='Конфигурация',exact=True).click();page.get_by_role('button',name='Общие настройки',exact=True).click()
    expect(page.get_by_role('spinbutton',name='Температура',exact=True)).to_have_value('0.35')
    expect(page.get_by_role('combobox',name='Голос по умолчанию')).to_have_value('filipp')
    # Restore the original settings through the public editor API, preserving revision history.
    csrf=page.request.get('/api/me').json()['csrf'];live=page.request.get('/api/editor/config').json()
    result=page.request.put('/api/editor/config',headers={'Origin':os.getenv('TEST_BASE_URL','http://localhost:5179'),'X-CSRF-Token':csrf},data={'expected_revision':live['revision'],'config':original['config']})
    assert result.status==200
    assert len(page.request.get('/api/editor/config/versions').json())>=3
    assert errors==[],errors
    print(json.dumps({'live_configuration':'saved without restart','reload_persistence':'passed','mobile':'passed','browser_errors':errors,'original_config':'restored'}))
    browser.close()
