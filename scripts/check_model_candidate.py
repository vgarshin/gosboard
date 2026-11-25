"""Explicitly run bounded, synthetic provider checks for one planned model."""
import argparse
import asyncio
import json

from fastapi import HTTPException

from app.configuration import AppConfig
from app.providers import CLOUDRU_PREFIX, Providers
from app.rag import grounding_instruction, validate_rag_answer
from app.retrieval_query import QUERY_CONTRACT, validate_route
from app.settings import Settings


CANDIDATES = (
    'cloudru/GigaChat/GigaChat-3-Pro',
    'cloudru/ai-sage/GigaChat3-10B-A1.8B',
    'cloudru/ai-sage/GigaChat3.5-432B-A28B',
    'deepseek-v4.1-flash',
)
QUESTION = 'Сколько мест в читальном зале по регламенту учебного пространства?'
SOURCE = {
    'id': 'D1', 'type': 'document', 'content_kind': 'document_chunk',
    'title': 'Учебный регламент', 'excerpt': 'В читальном зале предусмотрено 17 мест.',
}


def messages(system, text):
    return [{'role': 'system', 'text': system}, {'role': 'user', 'text': text}]


async def check(provider, config):
    prompt = messages('Отвечай кратко на русском языке.', 'Сколько будет восемь плюс девять?')
    await provider.complete(prompt, config)
    print('Обычный ответ: OK', flush=True)

    chunks = []

    async def collect(delta):
        chunks.append(delta)

    streamed, _ = await provider.complete_stream(prompt, config, collect)
    if not chunks or ''.join(chunks) != streamed:
        raise ValueError('Потоковый ответ не совпал с полученными фрагментами')
    print('Потоковый ответ: OK', flush=True)

    for question, expected in [('Здравствуйте!', 'direct'), (QUESTION, 'search')]:
        payload = {'history': [], 'question': question, 'sources': {'documents': True, 'web': False},
                   'space_instruction': config.system_prompt}
        raw, _ = await provider.complete(messages(QUERY_CONTRACT, json.dumps(payload, ensure_ascii=False)), config)
        if validate_route(raw)['route'] != expected:
            raise ValueError(f'Неверный выбор маршрута, ожидался {expected}')
        print(f'Маршрут {expected}: OK', flush=True)

    sources = [SOURCE]
    system = config.system_prompt + '\n' + grounding_instruction(sources)
    system += '\nSOURCE_DATA=' + json.dumps(sources, ensure_ascii=False)
    raw, _ = await provider.complete(messages(system, QUESTION), config)
    answer, citations, supported = validate_rag_answer(raw, sources)
    if not supported or citations != sources or '17' not in answer:
        raise ValueError('Ответ не подтвердил число мест со ссылкой на источник D1')
    print('Ответ с проверяемой ссылкой: OK', flush=True)


async def report_provider_status(response):
    if not response.is_success:
        print(f'HTTP провайдера: {response.status_code}', flush=True)


async def run(parser, model):
    settings = Settings()
    if settings.demo:
        parser.error('Для проверки API требуется DEMO_MODE=false')
    if model.startswith(CLOUDRU_PREFIX):
        if not settings.cloudru_api_key:
            parser.error('Заполните CLOUDRU_API_KEY')
    elif not all((settings.folder, settings.api_key)):
        parser.error('Заполните YANDEX_CLOUD_FOLDER_ID и YANDEX_AI_API_KEY')
    if not settings.budget_reviewed:
        parser.error('Проверьте тарифы и укажите PROVIDER_BUDGET_REVIEWED=true')

    try:
        config = AppConfig.model_validate_json(settings.config_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        parser.error('Проверьте CONFIG_PATH и формат конфигурации приложения')
    config = config.model_copy(update={
        'model': model, 'temperature': 0, 'max_tokens': min(config.max_tokens, 384),
        'reasoning_effort': 'default',
    })
    provider = Providers(settings)
    provider.client.event_hooks['response'].append(report_provider_status)
    try:
        await check(provider, config)
    except HTTPException as error:
        parser.exit(1, f'Проверка прервана (HTTP {error.status_code}): {error.detail}\n')
    except ValueError as error:
        parser.exit(1, f'Проверка прервана: {error}\n')
    finally:
        await provider.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('model', choices=CANDIDATES)
    parser.add_argument('--allow-paid', action='store_true',
                        help='Подтвердить до пяти платных вызовов API вне лимитов приложения')
    args = parser.parse_args()
    if not args.allow_paid:
        parser.error('Для платной проверки укажите --allow-paid')
    asyncio.run(run(parser, args.model))


if __name__ == '__main__':
    main()
