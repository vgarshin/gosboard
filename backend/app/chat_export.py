"""Portable, owner-authorized transcript rendering; no provider requests."""
import json
from datetime import datetime, timezone
from urllib.parse import urljoin


def export_chat(chat, messages, origin, format):
    def stamp(value):return datetime.fromtimestamp(value,timezone.utc).isoformat()
    messages=json.loads(json.dumps(messages))
    for message in messages:
        for source in message['metadata'].get('citations',[]):
            source['url']=urljoin(origin,source.get('url',''))
    document={'schema_version':1,'exported_at':datetime.now(timezone.utc).isoformat(),'chat':{key:chat[key] for key in ('id','title','created','updated','settings')},'messages':messages}
    if format=='json':return json.dumps(document,ensure_ascii=False,indent=2),'application/json'
    markdown=format=='md'
    lines=[('# ' if markdown else '')+chat['title'],'','Экспорт AI Talks · '+document['exported_at'],'']
    for message in messages:
        role={'user':'Вы','assistant':'AI Talks'}.get(message['role'],message['role'])
        lines.extend([('## ' if markdown else '')+role+' · '+stamp(message['created']),'',message['text'],''])
        for source in message['metadata'].get('citations',[]):
            url=urljoin(origin,source.get('url',''))
            if source.get('type')=='web':
                lines.append('Источник: '+('текст страницы' if source.get('content_kind')=='page_excerpt' else 'поисковый фрагмент'))
            lines.extend([f"[{source['id']}] {source['title']}"+(f" · стр. {source['page']}" if source.get('page') else ''),url,source.get('excerpt',''),''])
            if source.get('type')!='web':lines.extend(['Ссылка на RAG требует входа и доступного документа; фрагмент сохранён выше.',''])
    return '\n'.join(lines), 'text/markdown' if markdown else 'text/plain'
