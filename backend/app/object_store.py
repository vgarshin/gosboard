"""Private document objects; no public or presigned URLs are returned to the browser."""
import asyncio
from pathlib import Path
import shutil

from fastapi import HTTPException


class DocumentObjects:
    def __init__(self, settings):
        self.settings = settings
        self.client = None
        if settings.rag_enabled and not settings.demo:
            import boto3
            from botocore.config import Config
            self.client = boto3.client('s3', endpoint_url=settings.s3_endpoint,
                region_name=settings.s3_region, aws_access_key_id=settings.s3_key,
                aws_secret_access_key=settings.s3_secret,
                config=Config(connect_timeout=10, read_timeout=30,
                              retries={'max_attempts': 2, 'mode': 'standard'}))
        self.demo_dir = settings.data_dir / 'demo-objects'

    async def put(self, key, path, mime):
        def operation():
            if self.settings.demo:
                target = self.demo_dir / key
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(path, target)
                target.chmod(0o600)
            else:
                self.client.upload_file(str(path), self.settings.s3_bucket, key, ExtraArgs={'ContentType': mime})
        await self._run(operation)

    async def get(self, key, target):
        def operation():
            if self.settings.demo:
                shutil.copyfile(self.demo_dir / key, target)
            else:
                size = self.client.head_object(Bucket=self.settings.s3_bucket, Key=key)['ContentLength']
                if size > 10 * 1024 * 1024:
                    raise ValueError('Document exceeds upload limit')
                self.client.download_file(self.settings.s3_bucket, key, str(target))
        await self._run(operation)

    async def delete(self, key):
        def operation():
            if self.settings.demo:
                (self.demo_dir / key).unlink(missing_ok=True)
            else:
                self.client.delete_object(Bucket=self.settings.s3_bucket, Key=key)
        await self._run(operation)

    async def _run(self, operation):
        try:
            await asyncio.to_thread(operation)
        except Exception:
            raise HTTPException(502, 'Хранилище документов недоступно. Повторите позже') from None
