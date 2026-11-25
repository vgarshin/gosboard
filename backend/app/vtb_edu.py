"""Discovery and authorization attempts for the VTB Edu Identity Hub application."""
import base64
from dataclasses import dataclass
import secrets
import time
from urllib.parse import urlsplit

import httpx
import jwt

from .store import digest


def identity_hub_url(value, field):
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        raise ValueError(f'Некорректный URL {field}')
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or parsed.netloc != 'auth.yandex.cloud' or parsed.fragment:
        raise ValueError(f'URL {field} должен использовать https://auth.yandex.cloud')
    return value


@dataclass(frozen=True)
class Discovery:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    userinfo_endpoint: str
    jwks_uri: str


async def discover(client, url, expected_issuer):
    identity_hub_url(url, 'OpenID Configuration')
    identity_hub_url(expected_issuer, 'issuer')
    try:
        response = await client.get(url, follow_redirects=False, timeout=10)
        response.raise_for_status()
        document = response.json()
    except (httpx.HTTPError, ValueError):
        raise ValueError('Не удалось прочитать OpenID Configuration VTB Edu') from None
    if not isinstance(document, dict) or document.get('issuer') != expected_issuer:
        raise ValueError('Issuer в OpenID Configuration не совпадает с VTB_EDU_ISSUER')
    algorithms = document.get('id_token_signing_alg_values_supported')
    if not isinstance(algorithms, list) or 'RS256' not in algorithms:
        raise ValueError('OpenID Configuration VTB Edu не объявляет RS256')
    endpoints = ('authorization_endpoint', 'token_endpoint', 'userinfo_endpoint', 'jwks_uri')
    return Discovery(expected_issuer, **{
        field: identity_hub_url(document.get(field), field) for field in endpoints
    })



@dataclass(frozen=True)
class AuthorizationAttempt:
    state: str
    verifier: str
    nonce: str

    @property
    def challenge(self):
        hashed = bytes.fromhex(digest(self.verifier))
        return base64.urlsafe_b64encode(hashed).decode('ascii').rstrip('=')


def begin_attempt(store):
    browser = secrets.token_urlsafe(32)
    attempt = AuthorizationAttempt(*(secrets.token_urlsafe(32) for _ in range(3)))
    with store.connection() as db:
        db.execute(
            'INSERT INTO vtb_edu_oauth (hash,state,verifier,nonce,expires) VALUES (?,?,?,?,?)',
            (digest(browser), attempt.state, attempt.verifier, attempt.nonce, time.time() + 600),
        )
    return browser, attempt


def consume_attempt(store, browser, state):
    if not browser or not state or len(browser) > 128 or len(state) > 128 or not state.isascii():
        return None
    browser_hash = digest(browser)
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM vtb_edu_oauth WHERE hash=?', (browser_hash,)).fetchone()
        if row is None or row['expires'] <= time.time() or not secrets.compare_digest(row['state'], state):
            return None
        db.execute('DELETE FROM vtb_edu_oauth WHERE hash=?', (browser_hash,))
        return AuthorizationAttempt(row['state'], row['verifier'], row['nonce'])


CALLBACK = '/api/auth/vtb-edu/callback'
START = '/api/auth/vtb-edu'
ATTEMPT_COOKIE = 'gosboard_vtb_edu_oauth'


def authorization_url(discovery, client_id, redirect_uri, attempt):
    url = httpx.URL(discovery.authorization_endpoint)
    return str(url.copy_merge_params({
        'response_type': 'code', 'client_id': client_id, 'redirect_uri': redirect_uri,
        'scope': 'openid email profile', 'state': attempt.state, 'nonce': attempt.nonce,
        'code_challenge': attempt.challenge, 'code_challenge_method': 'S256',
    }))


class IdentityError(ValueError):
    """A fixed diagnostic code safe to include in the authentication audit."""


async def read_object(client, stage, method, url, **kwargs):
    try:
        response = await client.request(method, url, timeout=10, follow_redirects=False, **kwargs)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        raise IdentityError(stage + '_failed') from None
    if not isinstance(payload, dict):
        raise IdentityError(stage + '_invalid_response')
    return payload


async def exchange_identity(client, discovery, settings, code, attempt):
    token = await read_object(client, 'token_exchange', 'POST', discovery.token_endpoint, data={
        'grant_type': 'authorization_code', 'code': code,
        'redirect_uri': settings.origin + CALLBACK, 'code_verifier': attempt.verifier,
        'client_id': settings.vtb_edu_client_id, 'client_secret': settings.vtb_edu_client_secret,
    })
    encoded = token.get('id_token')
    if not isinstance(encoded, str) or len(encoded) > 16384:
        raise IdentityError('missing_id_token')
    header = jwt.get_unverified_header(encoded)
    key_id = header.get('kid')
    if not isinstance(key_id, str) or not key_id or header.get('alg') != 'RS256':
        raise IdentityError('unexpected_signing_header')
    jwks = await read_object(client, 'jwks', 'GET', discovery.jwks_uri)
    keys = jwks.get('keys')
    if not isinstance(keys, list):
        raise IdentityError('invalid_jwks')
    matching = [key for key in keys if isinstance(key, dict) and key.get('kid') == key_id
                and key.get('kty') == 'RSA' and key.get('alg', 'RS256') == 'RS256'
                and key.get('use', 'sig') == 'sig']
    if len(matching) != 1:
        raise IdentityError('unknown_signing_key')
    claims = jwt.decode(
        encoded, jwt.PyJWK.from_dict(matching[0], algorithm='RS256').key,
        algorithms=['RS256'], audience=settings.vtb_edu_client_id, issuer=discovery.issuer,
        options={'require': ['iss', 'sub', 'aud', 'exp', 'iat', 'nonce']}, leeway=30,
    )
    subject, nonce, audience = claims['sub'], claims['nonce'], claims['aud']
    authorized_party = claims.get('azp')
    if not subject or len(subject) > 255 or nonce != attempt.nonce:
        raise IdentityError('subject_or_nonce_mismatch')
    if (authorized_party is not None or isinstance(audience, list) and len(audience) > 1
            ) and authorized_party != settings.vtb_edu_client_id:
        raise IdentityError('authorized_party_mismatch')

    profile = claims
    if not isinstance(profile.get('email'), str) or profile.get('email_verified') is not True:
        access_token = token.get('access_token')
        if not isinstance(access_token, str) or not access_token:
            raise IdentityError('missing_userinfo_token')
        profile = await read_object(client, 'userinfo', 'GET', discovery.userinfo_endpoint,
                                    headers={'Authorization': f'Bearer {access_token}'})
        if profile.get('sub') != subject:
            raise IdentityError('userinfo_subject_mismatch')
    email = profile.get('email')
    if not isinstance(email, str):
        raise IdentityError('missing_corporate_email')
    if profile.get('email_verified') is not True:
        if 'email_verified' in profile or claims.get('email_verified') is False:
            raise IdentityError('email_unverified')
        expected_subject = settings.vtb_edu_pinned_subjects.get(email.strip().lower())
        if expected_subject != subject:
            raise IdentityError('missing_email_verification')
    name = profile.get('name')
    return subject, email, name.strip()[:200] if isinstance(name, str) and name.strip() else email
