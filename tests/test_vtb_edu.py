import asyncio

import httpx
import pytest

from app.access import Access
from app.settings import Settings
from app.store import Store, digest
from app.vtb_edu import AuthorizationAttempt, begin_attempt, consume_attempt, discover


HOST = 'https://auth.yandex.cloud'
ISSUER = HOST + '/synthetic-issuer'
URL = ISSUER + '/.well-known/openid-configuration'
METADATA = {
    'issuer': ISSUER,
    'authorization_endpoint': HOST + '/authorize',
    'token_endpoint': HOST + '/token',
    'userinfo_endpoint': HOST + '/userinfo',
    'jwks_uri': HOST + '/keys',
    'id_token_signing_alg_values_supported': ['RS256'],
}


def fetch(metadata, url=URL, redirect=False):
    def response(request):
        assert str(request.url) == URL
        if redirect:
            return httpx.Response(302, headers={'location': HOST + '/other'})
        return httpx.Response(200, json=metadata)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(response), follow_redirects=True) as client:
            return await discover(client, url, ISSUER)

    return asyncio.run(run())


def test_discovery_accepts_pinned_issuer_and_endpoints():
    assert fetch(METADATA).jwks_uri == METADATA['jwks_uri']


@pytest.mark.parametrize('changes', [
    {'issuer': ISSUER + '/'},
    {'jwks_uri': 'https://auth.yandex.cloud.example/keys'},
    {'token_endpoint': 'https://auth.yandex.cloud@other.example/token'},
    {'id_token_signing_alg_values_supported': 'RS256'},
])
def test_discovery_rejects_untrusted_metadata(changes):
    with pytest.raises(ValueError):
        fetch({**METADATA, **changes})


def test_discovery_does_not_follow_redirects():
    with pytest.raises(ValueError):
        fetch(METADATA, redirect=True)


def test_discovery_rejects_foreign_host_before_request():
    with pytest.raises(ValueError):
        fetch(METADATA, url='https://other.example/config')



def test_oidc_attempt_is_browser_bound_and_single_use(tmp_path):
    store = Store(tmp_path)
    browser, attempt = begin_attempt(store)
    assert consume_attempt(store, 'another-browser', attempt.state) is None
    assert consume_attempt(store, browser, 'wrong-state') is None
    assert consume_attempt(store, browser, attempt.state) == attempt
    assert consume_attempt(store, browser, attempt.state) is None


def test_oidc_attempt_expires_and_stays_separate_from_yandex(tmp_path):
    store = Store(tmp_path)
    browser, attempt = begin_attempt(store)
    with store.connection() as db:
        db.execute('UPDATE vtb_edu_oauth SET expires=0')
        db.execute('INSERT INTO oauth VALUES (?,?,?,?)',
                   (digest(browser), attempt.state, attempt.verifier, 9999999999))
    assert consume_attempt(store, browser, attempt.state) is None
    with store.connection() as db:
        assert db.execute('SELECT 1 FROM oauth WHERE hash=?', (digest(browser),)).fetchone()


def test_pkce_s256_matches_rfc_7636_example():
    attempt = AuthorizationAttempt('state', 'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk', 'nonce')
    assert attempt.challenge == 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM'



def test_corporate_admission_requires_exact_domain_and_starts_pending(tmp_path):
    store = Store(tmp_path)
    access = Access(store, Settings(demo=False, allowed=set(), admins=set()))
    assert not access.vtb_edu_identity(ISSUER, 'subject', 'person@example.test', 'Person')
    assert not access.vtb_edu_identity(ISSUER, 'subject', 'person@sub.vtb.education', 'Person')
    uid = access.vtb_edu_identity(ISSUER, 'subject', 'Person@VTB.EDUCATION', 'Person')
    assert uid.startswith('vtb-edu:')
    assert access.account('person@vtb.education')['enabled'] == -1
    assert access.vtb_edu_identity(ISSUER, 'subject', 'person@vtb.education', 'Updated') == uid
    assert not access.vtb_edu_identity(ISSUER, 'different-subject', 'person@vtb.education', 'Person')


def test_corporate_admission_honors_admin_preapproval(tmp_path):
    email = 'existing@vtb.education'
    store = Store(tmp_path)
    access = Access(store, Settings(demo=False, allowed={email}, admins={email}))
    assert access.vtb_edu_identity(ISSUER, 'subject', email, 'Person')
    assert access.account(email)['role'] == 'admin'
    assert access.account(email)['enabled'] == 1


@pytest.mark.parametrize('invalid', [None, 'issuer', 'audience', 'nonce', 'signature', 'domain', 'email_verified', 'userinfo_subject', 'pinned', 'wrong_pin', 'pinned_unverified'])
def test_oidc_callback_admission(tmp_path, invalid):
    import time
    from urllib.parse import parse_qs

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.providers import Providers
    from test_app import live_settings

    settings = live_settings(tmp_path)
    settings.vtb_edu_enabled = True
    settings.vtb_edu_discovery_url = URL
    settings.vtb_edu_issuer = ISSUER
    settings.vtb_edu_client_id = 'test-client'
    settings.vtb_edu_client_secret = 'test-secret'
    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    jwk['kid'] = 'synthetic'
    nonce = ''
    calls = []
    email = 'pilot@vtb.education'
    if invalid in {'pinned', 'wrong_pin', 'pinned_unverified'}:
        settings.vtb_edu_pinned_subjects[email] = 'other' if invalid == 'wrong_pin' else 'corporate-subject'

    def respond(request):
        address = str(request.url)
        calls.append(address)
        if address == URL:
            return httpx.Response(200, json=METADATA)
        if address == METADATA['jwks_uri']:
            return httpx.Response(200, json={'keys': [jwk]})
        if address == METADATA['userinfo_endpoint']:
            profile = {'sub': 'other' if invalid == 'userinfo_subject' else 'corporate-subject', 'email': email}
            if invalid not in {'pinned', 'wrong_pin'}:
                profile['email_verified'] = invalid not in {'email_verified', 'pinned_unverified'}
            return httpx.Response(200, json=profile)
        assert address == METADATA['token_endpoint']
        data = parse_qs(request.content.decode())
        assert data['client_secret'] == ['test-secret']
        assert data['redirect_uri'] == [settings.origin + '/api/auth/vtb-edu/callback']
        assert AuthorizationAttempt('', data['code_verifier'][0], '').challenge == code_challenge
        claims = {'iss': ISSUER, 'sub': 'corporate-subject', 'aud': 'test-client', 'iat': int(time.time()),
                  'exp': int(time.time()) + 300, 'nonce': nonce, 'email': email, 'email_verified': True}
        for case, claim, value in (
            ('issuer', 'iss', HOST + '/wrong'), ('audience', 'aud', 'other'), ('nonce', 'nonce', 'other'),
            ('domain', 'email', 'other@example.test'), ('email_verified', 'email_verified', False),
            ('userinfo_subject', 'email_verified', False),
        ):
            if invalid == case:
                claims[claim] = value
        if invalid in {'pinned', 'wrong_pin', 'pinned_unverified'}:
            claims.pop('email_verified')
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048) if invalid == 'signature' else signing_key
        encoded = jwt.encode(claims, key, algorithm='RS256', headers={'kid': 'synthetic'})
        return httpx.Response(200, json={'id_token': encoded, 'access_token': 'synthetic'})

    provider = Providers(settings)
    asyncio.run(provider.close())
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    app = create_app(settings, provider)
    with TestClient(app, base_url=settings.origin, follow_redirects=False) as client:
        bootstrap = client.get('/api/bootstrap').json()
        assert bootstrap['login_url'] == '/api/auth/yandex'
        start = client.get(bootstrap['vtb_edu_login_url'])
        assert start.status_code == 303
        parameters = httpx.URL(start.headers['location']).params
        nonce, code_challenge = parameters['nonce'], parameters['code_challenge']
        assert parameters['code_challenge_method'] == 'S256'
        callback = '/api/auth/vtb-edu/callback'
        params = {'code': 'synthetic-code', 'state': parameters['state']}
        result = client.get(callback, params=params)
        valid = invalid in {None, 'pinned'}
        assert result.headers['location'] == ('/' if valid else '/?auth_error=vtb-edu')
        count = len(calls)
        assert client.get(callback, params=params).headers['location'] == '/?auth_error=vtb-edu'
        assert len(calls) == count
        if not valid:
            assert client.get('/api/me').status_code == 401
            return
        account = client.get('/api/me').json()
        assert account['status'] == 'pending'
        assert client.get('/api/spaces').status_code == 403
        token = client.cookies['gosboard_session']
        admin_id = 'yandex:synthetic-admin'
        admin_email = next(iter(settings.admins))
        app.state.store.user(admin_id, admin_email, 'Admin')
        admin_token = app.state.store.session(admin_id, 60)
        client.cookies.clear()
        client.cookies.set('gosboard_session', admin_token)
        headers = {'Origin': settings.origin, 'X-CSRF-Token': app.state.store.auth(admin_token)['csrf']}
        listing = client.get('/api/admin/users').json()
        assert any(a['email'] == email and a['login_method'] == 'vtb-edu' for a in listing)
        assert client.put('/api/admin/users', json={'email': email, 'role': 'user'}, headers=headers).status_code == 200
        client.cookies.clear()
        client.cookies.set('gosboard_session', token)
        assert client.get('/api/me').json()['status'] == 'active'
