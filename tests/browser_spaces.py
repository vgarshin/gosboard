"""Spaces smoke for the isolated demo. Start tests/live_preview.py and Vite first."""
import argparse
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright

from browser_live_fixture import install_microphone


def verify(origin):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path='/usr/bin/google-chrome')
        context = browser.new_context(viewport={'width': 1280, 'height': 850})
        page = context.new_page()
        install_microphone(page, pcm=b'\x00' * 16000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        assert context.request.get(f'{origin}/api/bootstrap').json()['demo']
        page.goto(origin)
        page.get_by_role('button', name='Открыть локальное демо').click()
        expect(page.get_by_role('heading', name='Ваши пространства')).to_be_visible()
        csrf = context.request.get(f'{origin}/api/me').json()['csrf']

        def call(path, body, method='POST'):
            response = context.request.fetch(f'{origin}/api{path}', method=method, data=body,
                                             headers={'Origin':origin,'X-CSRF-Token':csrf})
            assert response.ok, response.text()
            return response.json()

        name = f'Другая среда {uuid4().hex[:8]}'
        description = 'Независимый разговор'
        sid = call('/management/spaces', {'name':name,'description':description})['id']
        call(f'/management/spaces/{sid}/assignments', {'email':'demo@localhost','assigned':True}, 'PUT')
        page.reload()
        expect(page.get_by_role('heading', name='Ваши пространства')).to_be_visible()
        assert page.locator('header .header-right button').evaluate_all('(buttons) => buttons.map(button => button.ariaLabel)') == [
            'Управление', 'Базы знаний', 'Пользователи', 'Настройки', 'Руководство пользователя', 'Выйти',
        ]
        for label, tabs in [('Базы знаний', ['Существующие базы', 'Новая база']),
                            ('Пользователи', ['Пользователи', 'Загрузить список', 'Разговоры']),
                            ('Управление', ['Существующие пространства', 'Новое пространство'])]:
            page.get_by_role('button', name=label, exact=True).click()
            expect(page.get_by_role('tab', name=tabs[0], exact=True)).to_have_attribute('aria-selected', 'true')
            page.get_by_role('tab', name=tabs[0], exact=True).focus()
            page.keyboard.press('ArrowRight')
            expect(page.get_by_role('tab', name=tabs[1])).to_have_attribute('aria-selected', 'true')
            page.keyboard.press('Escape')
        email = f'browser-{uuid4().hex[:8]}@example.org'
        call('/admin/users', {'email':email})
        page.get_by_role('button', name='Пользователи', exact=True).click()
        account = page.locator('.account-card').filter(has_text=email)
        account.get_by_role('button', name='Назначить пространства').click()
        account.get_by_role('checkbox', name=name).check()
        expect(page.get_by_text('Назначение сохранено')).to_be_visible()
        assigned = context.request.get(f'{origin}/api/management/space-accounts').json()
        assert sid in next(account['memberships'] for account in assigned if account['email'] == email)
        page.keyboard.press('Escape')
        page.get_by_role('button', name='Настройки', exact=True).click()
        expect(page.get_by_role('heading', name='Доступные модели')).to_be_visible()
        expect(page.get_by_role('tab', name='Промпты')).to_have_count(0)
        page.get_by_role('tab', name='Журнал').click()
        expect(page.get_by_text('space.assignment').first).to_be_visible()
        page.keyboard.press('Escape')
        page.get_by_role('button', name='Основное пространство').focus()
        page.keyboard.press('Enter')
        message = page.get_by_role('textbox', name='Сообщение')
        mode = page.get_by_role('combobox', name='Режим общения')
        assert mode.bounding_box()['x'] >= message.bounding_box()['x'] + message.bounding_box()['width']
        assert page.locator('header .header-right button').evaluate_all('(buttons) => buttons.map(button => button.ariaLabel)') == [
            'Руководство пользователя', 'Выйти из пространства',
        ]
        message.fill('Черновик первой среды')
        page.get_by_role('complementary').get_by_role('link', name='AI Talks · Ваши пространства').click()
        expect(page.get_by_role('button', name=name)).to_contain_text(description)
        page.get_by_role('button', name=name).click()
        expect(message).to_have_value('')
        message.fill('Синтетический вопрос')
        page.get_by_role('button', name='Отправить сообщение').click()
        expect(page.get_by_role('button', name='Копировать', exact=True)).to_be_visible(timeout=12000)

        second = context.new_page()
        second.set_viewport_size({'width':390,'height':844})
        second.goto(origin)
        second.get_by_role('button', name=name).click()
        expect(second.get_by_role('button', name='Копировать', exact=True)).to_be_visible()
        assert second.evaluate('document.documentElement.scrollWidth <= innerWidth')

        page.bring_to_front()
        page.get_by_role('combobox', name='Режим общения').select_option('voice')
        expect(page.get_by_role('button', name='Отправить сообщение', exact=True)).to_have_count(0)
        page.get_by_role('combobox', name='Режим общения').select_option('live')
        expect(page.get_by_role('button', name='Отправить сообщение', exact=True)).to_have_count(0)
        expect(page.get_by_role('button', name='Начать живой диалог')).to_be_visible()
        assert page.evaluate('liveTest.opens') == 0
        page.get_by_role('button', name='Начать живой диалог').click()
        page.wait_for_function('liveTest.active === 1')
        page.get_by_role('button', name='Выйти из пространства').click()
        page.wait_for_function('liveTest.active === 0')
        page.get_by_role('button', name='Основное пространство').click()
        expect(message).to_have_value('Черновик первой среды')
        expect(second.get_by_role('button', name='Копировать', exact=True)).to_be_visible()

        page.get_by_role('button', name='Выйти из пространства').click()
        page.get_by_role('button', name='Управление', exact=True).click()
        expect(page.get_by_role('dialog', name='Управление пространствами')).to_be_visible()
        page.keyboard.press('Escape')
        expect(page.get_by_role('button', name='Управление', exact=True)).to_be_focused()
        assert not errors, errors
        spaces = context.request.get(f'{origin}/api/management/spaces').json()
        revision = next(space['revision'] for space in spaces if space['id'] == sid)
        call(f'/management/spaces/{sid}/archive', {'name': name, 'revision': revision})
        browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://localhost:5196')
    verify(parser.parse_args().url.rstrip('/'))
    print('Spaces browser smoke passed')
