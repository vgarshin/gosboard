from dataclasses import dataclass, field
import json
import re
import os
from pathlib import Path
from urllib.parse import urlparse


def emails(name):
    return {v.strip().lower() for v in os.getenv(name, '').split(',') if v.strip()}


def vtb_edu_subjects():
    raw = os.getenv('VTB_EDU_PINNED_SUBJECTS') or '{}'
    try:
        mapping = json.loads(raw)
        if not isinstance(mapping, dict) or any(
            not re.fullmatch(r'[^\s@<>]+@vtb\.education', email)
            or email != email.lower() or not isinstance(subject, str)
            or not 1 <= len(subject) <= 255 or subject != subject.strip()
            for email, subject in mapping.items()
        ) or len(set(mapping.values())) != len(mapping):
            raise ValueError()
        return mapping
    except ValueError:
        raise ValueError('Invalid VTB_EDU_PINNED_SUBJECTS') from None


@dataclass
class Settings:
    environment: str = field(default_factory=lambda: os.getenv('APP_ENV', 'production'))
    origin: str = field(default_factory=lambda: os.getenv('APP_ORIGIN', 'https://aitalks.tech').rstrip('/'))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv('DATA_DIR', '/data')))
    config_path: Path = field(default_factory=lambda: Path(os.getenv('CONFIG_PATH', 'config/app.json')))
    demo: bool = field(default_factory=lambda: os.getenv('DEMO_MODE', 'false').lower() == 'true')
    allowed: set = field(default_factory=lambda: emails('ALLOWED_EMAILS'))
    admins: set = field(default_factory=lambda: emails('ADMIN_EMAILS'))
    oauth_id: str = field(default_factory=lambda: os.getenv('YANDEX_OAUTH_CLIENT_ID', ''))
    oauth_secret: str = field(default_factory=lambda: os.getenv('YANDEX_OAUTH_CLIENT_SECRET', ''))
    vtb_edu_enabled: bool = field(default_factory=lambda: os.getenv('VTB_EDU_ENABLED', 'false').lower() == 'true')
    vtb_edu_client_id: str = field(default_factory=lambda: os.getenv('VTB_EDU_CLIENT_ID', ''))
    vtb_edu_client_secret: str = field(default_factory=lambda: os.getenv('VTB_EDU_CLIENT_SECRET', ''), repr=False)
    vtb_edu_discovery_url: str = field(default_factory=lambda: os.getenv('VTB_EDU_DISCOVERY_URL', ''))
    vtb_edu_issuer: str = field(default_factory=lambda: os.getenv('VTB_EDU_ISSUER', ''))
    vtb_edu_pinned_subjects: dict[str, str] = field(default_factory=vtb_edu_subjects, repr=False)
    folder: str = field(default_factory=lambda: os.getenv('YANDEX_CLOUD_FOLDER_ID', ''))
    api_key: str = field(default_factory=lambda: os.getenv('YANDEX_AI_API_KEY', ''))
    cloudru_api_key: str = field(default_factory=lambda: os.getenv('CLOUDRU_API_KEY', ''), repr=False)
    speech_key: str = field(default_factory=lambda: os.getenv('YANDEX_SPEECHKIT_API_KEY', ''))
    daily_limit: float = field(default_factory=lambda: float(os.getenv('DAILY_LIMIT_RUB', '500')))
    monthly_limit: float = field(default_factory=lambda: float(os.getenv('MONTHLY_LIMIT_RUB', '5000')))
    # Conservative per-call reservations, not provider-reported invoice amounts.
    llm_reserve: float = field(default_factory=lambda: float(os.getenv('LLM_RESERVE_RUB', '10')))
    stt_reserve: float = field(default_factory=lambda: float(os.getenv('STT_RESERVE_RUB', '5')))
    tts_reserve: float = field(default_factory=lambda: float(os.getenv('TTS_RESERVE_RUB', '20')))
    budget_reviewed: bool = field(default_factory=lambda: os.getenv('PROVIDER_BUDGET_REVIEWED', 'false').lower() == 'true')
    web_enabled: bool = field(default_factory=lambda: os.getenv('WEB_SEARCH_ENABLED','false').lower()=='true')
    search_key: str = field(default_factory=lambda: os.getenv('YANDEX_SEARCH_API_KEY',''))
    search_folder: str = field(default_factory=lambda: os.getenv('YANDEX_SEARCH_FOLDER_ID','') or os.getenv('YANDEX_CLOUD_FOLDER_ID',''))
    search_reserve: float = field(default_factory=lambda: float(os.getenv('SEARCH_RESERVE_RUB','10')))
    search_ttl: int = field(default_factory=lambda: int(os.getenv('SEARCH_CACHE_TTL_SECONDS','600')))
    web_pages: bool = field(default_factory=lambda: os.getenv('WEB_PAGE_FETCH_ENABLED','true').lower()=='true')
    web_page_ttl: int = field(default_factory=lambda: int(os.getenv('WEB_PAGE_CACHE_TTL_SECONDS','600')))
    rag_enabled: bool = field(default_factory=lambda: os.getenv('RAG_ENABLED', 'false').lower() == 'true')
    s3_bucket: str = field(default_factory=lambda: os.getenv('BACKUP_S3_BUCKET', 'aitalks-data'))
    s3_endpoint: str = field(default_factory=lambda: os.getenv('BACKUP_S3_ENDPOINT', 'https://storage.yandexcloud.net'))
    s3_region: str = field(default_factory=lambda: os.getenv('AWS_DEFAULT_REGION', 'ru-central1'))
    s3_key: str = field(default_factory=lambda: os.getenv('AWS_ACCESS_KEY_ID', ''))
    s3_secret: str = field(default_factory=lambda: os.getenv('AWS_SECRET_ACCESS_KEY', ''))
    embedding_doc_model: str = field(default_factory=lambda: os.getenv('RAG_DOC_MODEL', 'text-search-doc/latest'))
    embedding_query_model: str = field(default_factory=lambda: os.getenv('RAG_QUERY_MODEL', 'text-search-query/latest'))
    embedding_reserve: float = field(default_factory=lambda: float(os.getenv('EMBEDDING_RESERVE_RUB', '0.1')))
    rag_min_score: float = field(default_factory=lambda: float(os.getenv('RAG_MIN_SCORE', '0.2')))
    rag_max_documents: int = 100
    rag_max_chunks: int = 10000
    audio_ttl: int = 86400
    cache_bytes: int = 1_000_000_000
    session_seconds: int = 7 * 86400

    @property
    def secure(self):
        return urlparse(self.origin).scheme == 'https'

    @property
    def vtb_edu_ready(self):
        return self.vtb_edu_enabled and not self.demo and self.secure and all((
            self.vtb_edu_client_id, self.vtb_edu_client_secret, self.vtb_edu_discovery_url, self.vtb_edu_issuer,
        ))

    def validate(self):
        parsed = urlparse(self.origin)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.path not in {'', '/'}:
            raise ValueError('APP_ORIGIN must be an HTTP(S) origin without a path')
        if self.demo and (self.environment != 'development' or parsed.hostname not in {'localhost', '127.0.0.1', '::1'}):
            raise ValueError('DEMO_MODE requires development and a loopback APP_ORIGIN')
        if not self.demo:
            required = [self.allowed, self.admins, self.oauth_id, self.oauth_secret, self.folder,
                        self.api_key, self.speech_key, self.budget_reviewed]
            if not all(required):
                raise ValueError('Configure Yandex credentials, allowlist/admins and PROVIDER_BUDGET_REVIEWED before live startup')
            if not self.admins <= self.allowed:
                raise ValueError('All ADMIN_EMAILS must also be in ALLOWED_EMAILS')
            if self.environment == 'production' and not self.secure:
                raise ValueError('Production requires HTTPS')
        if self.web_enabled and not self.demo and not all((self.search_key,self.search_folder)):
            raise ValueError('Web search requires the dedicated Search API key and folder ID')
        if not 30<=self.web_page_ttl<=3600:
            raise ValueError('Invalid web page cache TTL')
        if self.search_reserve<=0 or not 30<=self.search_ttl<=3600:
            raise ValueError('Invalid search reservation/cache TTL')
        if self.rag_enabled and not self.demo:
            if not all((self.s3_bucket, self.s3_key, self.s3_secret)) or not self.s3_endpoint.startswith('https://'):
                raise ValueError('RAG requires private S3 credentials and an HTTPS endpoint')
        if not -1 <= self.rag_min_score <= 1 or self.embedding_reserve <= 0:
            raise ValueError('Invalid RAG score or embedding reservation')
        if min(self.daily_limit, self.monthly_limit, self.llm_reserve, self.stt_reserve, self.tts_reserve) <= 0:
            raise ValueError('Budget limits and reservations must be positive')
