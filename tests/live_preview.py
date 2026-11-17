"""Reproducible isolated demo for browser_live_* tests; never loads production data.

From project root:
  PYTHONPATH=backend python tests/live_preview.py
  API_PROXY=http://127.0.0.1:8031 npm --prefix frontend run dev -- --port 5196
"""
import argparse
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from app.main import create_app
from app.providers import Providers
from app.settings import Settings


class SlowDemo(Providers):
    async def complete_stream(self,messages,config,delta):
        text,metadata=await self.complete(messages,config)
        for offset in range(0,len(text),25):
            await delta(text[offset:offset+25])
            await asyncio.sleep(.05)
        return text,metadata


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-port',type=int,default=8031)
    parser.add_argument('--web-port',type=int,default=5196)
    args=parser.parse_args()
    with TemporaryDirectory(prefix='gosboard-live-preview-') as directory:
        settings=Settings(environment='development',origin=f'http://localhost:{args.web_port}',
                          demo=True,rag_enabled=True,web_enabled=True,web_pages=False,
                          data_dir=Path(directory),config_path=Path(__file__).parents[1]/'config/app.json')
        uvicorn.run(create_app(settings,SlowDemo(settings)),host='127.0.0.1',port=args.api_port)
