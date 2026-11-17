"""Local-only role-management browser rehearsal; fake identities bypass OAuth only in this fixture."""
import json,os,secrets,sqlite3,time,hashlib
from pathlib import Path
from playwright.sync_api import sync_playwright,expect

base=os.getenv('TEST_BASE_URL','http://localhost:5177')
data=Path(os.getenv('TEST_DATA_DIR','/tmp/gosboard-roles-demo'))
def identity(context, uid,email):
    token,csrf=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
    with sqlite3.connect(data/'gosboard.sqlite3') as db:
        db.execute('INSERT OR REPLACE INTO users VALUES (?,?,?)',(uid,email,uid))
        db.execute('INSERT INTO sessions VALUES (?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),uid,csrf,time.time()+600))
    context.add_cookies([{'name':'gosboard_session','value':token,'url':base}])
    return {'Origin':base,'X-CSRF-Token':csrf}
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True)
    admin=browser.new_context(base_url=base,viewport={'width':1440,'height':1000});page=admin.new_page();errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)));page.goto('/')
    assert page.request.get('/api/bootstrap').json()['demo']
    page.get_by_role('button',name='Открыть локальное демо').click()
    page.get_by_role('button',name='Пользователи',exact=True).click()
    for email,role in [('editor@example.com','editor'),('user@example.com','user')]:
        page.get_by_role('button',name='Добавить пользователя',exact=True).click()
        page.get_by_role('textbox',name='Email пользователя').fill(email)
        page.get_by_role('combobox',name='Роль пользователя').select_option(role)
        page.get_by_role('button',name='Добавить',exact=True).click()
        expect(page.get_by_role('status')).to_have_text('Пользователь добавлен')
    page.screenshot(path='/tmp/gosboard-users.png',full_page=True)
    page.get_by_role('button',name='Закрыть пользователей').click()
    user=browser.new_context(base_url=base);headers=identity(user,'browser-user','user@example.com')
    cid=user.request.post('/api/chats',headers=headers).json()['id']
    response=user.request.post(f'/api/chats/{cid}/messages',headers=headers,data={'text':'PRIVATE BODY DO NOT EXPOSE','request_id':__import__('uuid').uuid4().hex})
    assert response.status==200
    with sqlite3.connect(data/'gosboard.sqlite3') as db:db.execute('UPDATE chats SET title=? WHERE id=?',('Настройка участника',cid))
    editor=browser.new_context(base_url=base);identity(editor,'browser-editor','editor@example.com')
    ep=editor.new_page();ep.on('pageerror',lambda e:errors.append(str(e)));ep.goto('/')
    ep.get_by_role('button',name='Конфигурация',exact=True).click()
    expect(ep.get_by_role('button',name='Пользователи',exact=True)).to_have_count(0)
    ep.get_by_role('combobox',name='Настраиваемый разговор').select_option(cid)
    ep.get_by_role('combobox',name='Модель разговора').select_option('alice-flash')
    ep.get_by_role('combobox',name='Голос разговора').select_option('filipp')
    expect(ep.get_by_role('combobox',name='Рассуждения разговора')).to_be_disabled()
    ep.get_by_role('button',name='Сохранить настройки разговора').click()
    expect(ep.get_by_role('status')).to_have_text('Сохранено')
    assert 'PRIVATE BODY' not in ep.locator('body').inner_text()
    assert editor.request.get(f'/api/chats/{cid}/messages').status==404
    ep.screenshot(path='/tmp/gosboard-editor.png',full_page=True)
    ep.get_by_role('button',name='Закрыть конфигурацию').click()
    ep.get_by_role('button',name='Базы знаний',exact=True).click()
    ep.get_by_role('textbox',name='Название новой базы').fill('База редактора')
    ep.get_by_role('button',name='Создать',exact=True).click()
    expect(ep.get_by_role('combobox',name='Управляемая база знаний')).not_to_have_value('')
    expect(ep.get_by_role('spinbutton',name='Top K',exact=True)).to_have_count(0)
    ep.get_by_role('button',name='Закрыть базы знаний').click()
    up=user.new_page();up.goto('/')
    expect(up.get_by_role('combobox',name='Модель',exact=True)).to_have_count(0)
    expect(up.get_by_role('combobox',name='Голос',exact=True)).to_have_count(0)
    expect(up.get_by_role('button',name='Конфигурация',exact=True)).to_have_count(0)
    assert user.request.get('/api/chats').json()[0]['settings']['voice']=='filipp'
    page.get_by_role('button',name='Конфигурация',exact=True).click()
    page.get_by_role('button',name='Настройки RAG',exact=True).click()
    page.get_by_role('combobox',name='Настраиваемая база знаний').select_option(label='База редактора')
    page.get_by_role('spinbutton',name='Top K',exact=True).fill('2')
    with page.expect_response(lambda r:'/policy' in r.url and r.request.method=='PUT') as saved:
        page.get_by_role('button',name='Сохранить настройки RAG').click()
    assert saved.value.status==200
    page.get_by_role('button',name='Закрыть конфигурацию').click()
    page.get_by_role('button',name='Пользователи',exact=True).click()
    page.get_by_role('switch',name='Доступ: user@example.com',exact=True).uncheck()
    expect(page.get_by_role('status')).to_have_text('Доступ отключён; сессии завершены')
    assert user.request.get('/api/me').status==401
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path='/tmp/gosboard-users-mobile.png',full_page=True)
    assert errors==[],errors
    print(json.dumps({'browser_errors':errors,'passed':['admin user roles','editor metadata-only model/voice change','reasoning capability','user model restriction','editor base creation','admin-only RAG tuning','session revocation','mobile management']},ensure_ascii=False))
    browser.close()
