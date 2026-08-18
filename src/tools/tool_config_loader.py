import logging
import os
from typing import Any, Dict, Optional

import httpx
from dotenv import load_dotenv

load_dotenv(".env.local")

logger = logging.getLogger("config_loader")

NEXTJS_API_URL = (
    os.getenv("NEXTJS_API_URL")
    or os.getenv("NEXT_PUBLIC_API_URL")
    or "http://localhost:3000"
).rstrip("/")

async def get_tool_config( tool_name: str, org_id: str, assistant_id: str  ) -> Optional[Dict[str, Any]]:
    logger.info(f"   🔍 Fetching config for tool: '{tool_name}'")
    logger.info(f"   • API URL: {NEXTJS_API_URL}/api/tools")
    logger.info(f"   • Org ID: {org_id}")
    logger.info(f"   • Assistant ID: {assistant_id}")

    try:
        params: Dict[str, str] = { "name": tool_name, "enabled": "true", "orgId": org_id, "assistantId": assistant_id}
        logger.info(f"   • Request params: {params}")

        async with httpx.AsyncClient(timeout=20.0) as client:
            logger.info("   → Making GET request to API...")
            response = await client.get(f"{NEXTJS_API_URL}/api/tools", params=params)
            logger.info(f"   ✓ Response received: Status {response.status_code}")

            response.raise_for_status()
            tools = response.json()
            logger.info("   ✓ Response parsed as JSON")
            logger.info(f"   • Tools returned: {len(tools) if isinstance(tools, list) else 'not a list'}")

            if isinstance(tools, list) and tools:
                logger.info(f"   → Searching for tool '{tool_name}' in {len(tools)} tools...")
                for tool in tools:
                    if tool.get("name") == tool_name:
                        config = tool.get("config")
                        logger.info(f"   ✓ Tool '{tool_name}' found!")
                        logger.info(f"   • Config keys: {list(config.keys()) if config else 'None'}")
                        return config
                logger.warning( f"   ⚠️  Tool '{tool_name}' not found in response for org {org_id}, assistant {assistant_id}" )
            else:
                logger.warning( f"   ⚠️  Tool '{tool_name}' not found or not initialized for org {org_id}, assistant {assistant_id}" )

            return None

    except httpx.TimeoutException:
        logger.error(f"Timeout loading {tool_name} config from API (timeout: 20.0s)")
        return None

    except httpx.RequestError as e:
        logger.error(f"Request error loading {tool_name} config: {e}")
        return None

    except httpx.HTTPStatusError as e:
        logger.error( f"HTTP error loading {tool_name} config: {e.response.status_code} - {e.response.text}" )
        return None

    except Exception as e:
        logger.error( f"Unexpected error loading {tool_name} config: {e}" )
        return None


async def get_transfer_call_config( org_id: str,  assistant_id: str ) -> Optional[Dict[str, Any]]:

    return await get_tool_config( tool_name="transfer_call", org_id=org_id, assistant_id=assistant_id )


async def get_custom_function_config( operation: str, org_id: str, assistant_id: str ) -> Optional[Dict[str, Any]]:

    return await get_tool_config( tool_name=operation, org_id=org_id, assistant_id=assistant_id )
