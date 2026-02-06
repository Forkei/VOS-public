"""
App Registry proxy router.
Proxies requests to the app_registry service for app discovery and health checks.
"""
import logging
import os
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query
import httpx

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/apps", tags=["apps"])

# Get app_registry URL from environment or use default
APP_REGISTRY_URL = os.getenv("APP_REGISTRY_URL", "http://app_registry:8010")


async def _proxy_to_registry(method: str, path: str, params: dict = None, json_data: dict = None) -> dict:
    """Proxy a request to the app_registry service."""
    url = f"{APP_REGISTRY_URL}{path}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if method == "GET":
                response = await client.get(url, params=params)
            elif method == "POST":
                response = await client.post(url, json=json_data)
            elif method == "PUT":
                response = await client.put(url, json=json_data)
            elif method == "DELETE":
                response = await client.delete(url)
            else:
                raise HTTPException(status_code=405, detail=f"Method {method} not supported")

            if response.status_code >= 400:
                logger.error(f"App registry returned error: {response.status_code} - {response.text}")
                raise HTTPException(status_code=response.status_code, detail=response.text)

            return response.json()
    except httpx.ConnectError as e:
        logger.error(f"Failed to connect to app registry at {url}: {e}")
        raise HTTPException(status_code=503, detail="App registry service unavailable")
    except httpx.TimeoutException as e:
        logger.error(f"Timeout connecting to app registry at {url}: {e}")
        raise HTTPException(status_code=504, detail="App registry service timeout")
    except Exception as e:
        logger.error(f"Error proxying to app registry: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
async def get_apps(
    status: Optional[str] = Query(None, description="Filter by status (healthy, unhealthy, degraded)"),
    tag: Optional[str] = Query(None, description="Filter by tag")
):
    """
    Get all registered apps with their health status.
    Proxies to app_registry service.
    """
    params = {}
    if status:
        params["status"] = status
    if tag:
        params["tag"] = tag

    return await _proxy_to_registry("GET", "/api/v1/apps", params=params)


@router.get("/{app_id}")
async def get_app(app_id: str):
    """
    Get a specific app by ID.
    Proxies to app_registry service.
    """
    return await _proxy_to_registry("GET", f"/api/v1/apps/{app_id}")


@router.get("/search")
async def search_apps(
    query: Optional[str] = Query(None, description="Search query"),
    tags: Optional[str] = Query(None, description="Comma-separated tags"),
    native_only: Optional[bool] = Query(None, description="Filter to native apps only"),
    limit: Optional[int] = Query(None, description="Max results")
):
    """
    Search apps by query, tags, or other criteria.
    Proxies to app_registry service.
    """
    params = {}
    if query:
        params["query"] = query
    if tags:
        params["tags"] = tags
    if native_only is not None:
        params["native_only"] = native_only
    if limit:
        params["limit"] = limit

    return await _proxy_to_registry("GET", "/api/v1/apps/search", params=params)


@router.post("")
async def register_app(manifest: dict):
    """
    Register a new app (admin only).
    Proxies to app_registry service.
    """
    return await _proxy_to_registry("POST", "/api/v1/apps", json_data=manifest)


@router.put("/{app_id}")
async def update_app(app_id: str, manifest: dict):
    """
    Update an existing app (admin only).
    Proxies to app_registry service.
    """
    return await _proxy_to_registry("PUT", f"/api/v1/apps/{app_id}", json_data=manifest)


@router.delete("/{app_id}")
async def unregister_app(app_id: str):
    """
    Unregister an app (admin only).
    Proxies to app_registry service.
    """
    return await _proxy_to_registry("DELETE", f"/api/v1/apps/{app_id}")
