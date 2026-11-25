"""Check Identity Hub metadata using only the public VTB Edu configuration."""
import asyncio
import os
import sys

import httpx

from app.vtb_edu import discover


async def main():
    async with httpx.AsyncClient() as client:
        try:
            await discover(client, os.getenv('VTB_EDU_DISCOVERY_URL'), os.getenv('VTB_EDU_ISSUER'))
        except ValueError as error:
            sys.exit(str(error))
    print('VTB Edu: issuer, адреса Identity Hub и поддержка RS256 подтверждены')


if __name__ == '__main__':
    asyncio.run(main())
