"""
VOS App Registry Service

Central registry for all VOS apps. Provides:
- App registration and discovery
- Health monitoring
- Real-time registry updates via WebSocket
- Proxy endpoints for app state and actions
"""

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Configuration
HEALTH_CHECK_INTERVAL = int(os.getenv("HEALTH_CHECK_INTERVAL", "10"))  # seconds (faster detection)
UNHEALTHY_THRESHOLD = int(os.getenv("UNHEALTHY_THRESHOLD", "2"))  # failed checks before unhealthy
INTERNAL_API_KEY_PATH = os.getenv("INTERNAL_API_KEY_PATH", "/shared/internal_api_key")


# Pydantic models
class RegisterRequest(BaseModel):
    """Request to register an app."""
    container_url: str
    manifest: Optional[Dict[str, Any]] = None


class ActionInvokeRequest(BaseModel):
    """Request to invoke an action on an app."""
    parameters: Dict[str, Any] = {}
    source: str = "registry"


class AppInfo(BaseModel):
    """Information about a registered app."""
    app_id: str
    container_url: str
    manifest: Dict[str, Any]
    status: str  # healthy, unhealthy, unknown
    registered_at: str
    last_health_check: Optional[str] = None
    health_check_failures: int = 0


@dataclass
class RegisteredApp:
    """Internal representation of a registered app."""
    app_id: str
    container_url: str
    manifest: Dict[str, Any]
    status: str = "unknown"
    registered_at: datetime = field(default_factory=datetime.utcnow)
    last_health_check: Optional[datetime] = None
    health_check_failures: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "app_id": self.app_id,
            "container_url": self.container_url,
            "manifest": self.manifest,
            "status": self.status,
            "registered_at": self.registered_at.isoformat(),
            "last_health_check": self.last_health_check.isoformat() if self.last_health_check else None,
            "health_check_failures": self.health_check_failures,
        }


class WebSocketConnection:
    """Manages a WebSocket connection for registry updates."""

    def __init__(self, websocket: WebSocket, session_id: Optional[str] = None):
        self.websocket = websocket
        self.session_id = session_id
        self.connected_at = datetime.utcnow()

    async def send(self, data: Dict[str, Any]) -> bool:
        """Send data to the client. Returns False if send fails."""
        try:
            await self.websocket.send_json(data)
            return True
        except Exception as e:
            logger.warning(f"Failed to send to WebSocket: {e}")
            return False


class AppRegistry:
    """
    Central registry for VOS apps.

    Manages app registration, health monitoring, and WebSocket notifications.
    """

    def __init__(self):
        self._apps: Dict[str, RegisteredApp] = {}
        self._ws_connections: List[WebSocketConnection] = []
        self._health_check_task: Optional[asyncio.Task] = None
        self._internal_api_key: Optional[str] = None
        self._load_internal_api_key()

    def _load_internal_api_key(self) -> None:
        """Load internal API key from file."""
        try:
            with open(INTERNAL_API_KEY_PATH, "r") as f:
                self._internal_api_key = f.read().strip()
            logger.info("Loaded internal API key")
        except Exception as e:
            logger.warning(f"Could not load internal API key: {e}")

    async def start_health_checker(self) -> None:
        """Start the background health check task."""
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        logger.info(f"Started health checker (interval: {HEALTH_CHECK_INTERVAL}s)")

    async def stop_health_checker(self) -> None:
        """Stop the background health check task."""
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
            logger.info("Stopped health checker")

    async def _health_check_loop(self) -> None:
        """Background loop that checks app health."""
        while True:
            try:
                await asyncio.sleep(HEALTH_CHECK_INTERVAL)
                await self._check_all_apps_health()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check loop error: {e}")

    async def _check_all_apps_health(self) -> None:
        """Check health of all registered apps."""
        async with httpx.AsyncClient(timeout=5.0) as client:
            for app_id, app in list(self._apps.items()):
                try:
                    health_endpoint = app.manifest.get("backend", {}).get("healthEndpoint", "/health")
                    response = await client.get(f"{app.container_url}{health_endpoint}")

                    if response.status_code == 200:
                        old_status = app.status
                        app.status = "healthy"
                        app.health_check_failures = 0
                        app.last_health_check = datetime.utcnow()

                        if old_status != "healthy":
                            await self._broadcast_app_status_change(app_id, "healthy")
                            logger.info(f"App {app_id} is now healthy")
                    else:
                        await self._handle_health_check_failure(app, f"Status {response.status_code}")

                except Exception as e:
                    await self._handle_health_check_failure(app, str(e))

    async def _handle_health_check_failure(self, app: RegisteredApp, reason: str) -> None:
        """Handle a failed health check."""
        app.health_check_failures += 1
        app.last_health_check = datetime.utcnow()

        if app.health_check_failures >= UNHEALTHY_THRESHOLD:
            if app.status != "unhealthy":
                app.status = "unhealthy"
                await self._broadcast_app_status_change(app.app_id, "unhealthy")
                logger.warning(f"App {app.app_id} is now unhealthy: {reason}")

    async def register_app(self, container_url: str, manifest: Optional[Dict[str, Any]] = None) -> RegisteredApp:
        """
        Register an app with the registry.

        If manifest is not provided, fetches it from the container.
        """
        # Fetch manifest if not provided
        if not manifest:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(f"{container_url}/manifest")
                    response.raise_for_status()
                    manifest = response.json()
            except Exception as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to fetch manifest from {container_url}: {e}"
                )

        # Extract app ID from manifest
        app_id = manifest.get("app", {}).get("id")
        if not app_id:
            raise HTTPException(
                status_code=400,
                detail="Manifest must contain app.id"
            )

        # Create or update registration
        existing = self._apps.get(app_id)
        if existing:
            existing.container_url = container_url
            existing.manifest = manifest
            existing.status = "unknown"
            existing.health_check_failures = 0
            logger.info(f"Updated registration for app: {app_id}")
            await self._broadcast_registry_event("app_updated", app_id, existing.to_dict())
            return existing
        else:
            app = RegisteredApp(
                app_id=app_id,
                container_url=container_url,
                manifest=manifest,
            )
            self._apps[app_id] = app
            logger.info(f"Registered new app: {app_id}")
            await self._broadcast_registry_event("app_registered", app_id, app.to_dict())
            return app

    async def unregister_app(self, app_id: str) -> bool:
        """Unregister an app from the registry."""
        if app_id not in self._apps:
            return False

        app = self._apps.pop(app_id)
        logger.info(f"Unregistered app: {app_id}")
        await self._broadcast_registry_event("app_unregistered", app_id, {"app_id": app_id})
        return True

    def get_app(self, app_id: str) -> Optional[RegisteredApp]:
        """Get a registered app by ID."""
        return self._apps.get(app_id)

    def list_apps(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[RegisteredApp]:
        """List registered apps with optional filtering."""
        apps = list(self._apps.values())

        if category:
            apps = [a for a in apps if a.manifest.get("app", {}).get("category") == category]

        if status:
            apps = [a for a in apps if a.status == status]

        return apps

    async def heartbeat(self, app_id: str, container_url: str) -> bool:
        """Process a heartbeat from an app."""
        app = self._apps.get(app_id)
        if not app:
            return False

        app.last_health_check = datetime.utcnow()
        if app.status != "healthy":
            app.status = "healthy"
            app.health_check_failures = 0
            await self._broadcast_app_status_change(app_id, "healthy")

        return True

    def add_ws_connection(self, connection: WebSocketConnection) -> None:
        """Add a WebSocket connection."""
        self._ws_connections.append(connection)
        logger.debug(f"Added WebSocket connection (total: {len(self._ws_connections)})")

    def remove_ws_connection(self, connection: WebSocketConnection) -> None:
        """Remove a WebSocket connection."""
        if connection in self._ws_connections:
            self._ws_connections.remove(connection)
            logger.debug(f"Removed WebSocket connection (total: {len(self._ws_connections)})")

    async def _broadcast_registry_event(
        self,
        event_type: str,
        app_id: str,
        data: Dict[str, Any],
    ) -> None:
        """Broadcast a registry event to all WebSocket clients."""
        message = {
            "type": "registry_event",
            "event": event_type,
            "app_id": app_id,
            "data": data,
            "timestamp": datetime.utcnow().isoformat(),
        }

        disconnected = []
        for conn in self._ws_connections:
            if not await conn.send(message):
                disconnected.append(conn)

        for conn in disconnected:
            self.remove_ws_connection(conn)

    async def _broadcast_app_status_change(self, app_id: str, status: str) -> None:
        """Broadcast an app status change."""
        app = self._apps.get(app_id)
        if app:
            await self._broadcast_registry_event(
                "app_status_changed",
                app_id,
                {"status": status, "app": app.to_dict()},
            )


# Global registry instance
registry = AppRegistry()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("Starting App Registry Service")
    await registry.start_health_checker()
    yield
    logger.info("Stopping App Registry Service")
    await registry.stop_health_checker()


# Create FastAPI app
app = FastAPI(
    title="VOS App Registry",
    description="Central registry for VOS apps",
    version="1.0.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Health endpoint
@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "registered_apps": len(registry._apps),
        "ws_connections": len(registry._ws_connections),
    }


# Registration endpoints
@app.post("/api/v1/apps/register")
async def register_app(request: RegisterRequest):
    """Register an app with the registry."""
    app = await registry.register_app(request.container_url, request.manifest)
    return {
        "status": "registered",
        "app_id": app.app_id,
        "app": app.to_dict(),
    }


@app.delete("/api/v1/apps/{app_id}")
async def unregister_app(app_id: str):
    """Unregister an app from the registry."""
    if registry.unregister_app(app_id):
        return {"status": "unregistered", "app_id": app_id}
    raise HTTPException(status_code=404, detail=f"App not found: {app_id}")


# Discovery endpoints
@app.get("/api/v1/apps")
async def list_apps(
    category: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
):
    """List all registered apps."""
    apps = registry.list_apps(category=category, status=status)
    return {"apps": [a.to_dict() for a in apps]}


@app.get("/api/v1/apps/{app_id}")
async def get_app(app_id: str):
    """Get a specific app by ID."""
    app = registry.get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail=f"App not found: {app_id}")
    return app.to_dict()


@app.get("/api/v1/apps/{app_id}/manifest")
async def get_app_manifest(app_id: str):
    """Get an app's manifest."""
    app = registry.get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail=f"App not found: {app_id}")
    return app.manifest


@app.get("/api/v1/apps/{app_id}/health")
async def get_app_health(app_id: str):
    """Get an app's health status."""
    app = registry.get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail=f"App not found: {app_id}")
    return {
        "app_id": app_id,
        "status": app.status,
        "last_check": app.last_health_check.isoformat() if app.last_health_check else None,
        "failures": app.health_check_failures,
    }


# Heartbeat endpoint
@app.post("/api/v1/apps/{app_id}/heartbeat")
async def heartbeat(app_id: str, request: RegisterRequest):
    """Process a heartbeat from an app."""
    if await registry.heartbeat(app_id, request.container_url):
        return {"status": "acknowledged"}
    raise HTTPException(status_code=404, detail=f"App not found: {app_id}")


# Proxy endpoints for app state and actions
@app.get("/api/v1/apps/{app_id}/state/{state_id}")
async def proxy_get_state(app_id: str, state_id: str):
    """Proxy state query to the app container."""
    app = registry.get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail=f"App not found: {app_id}")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{app.container_url}/api/v1/state/{state_id}")
            if response.status_code == 204:
                return {"state_id": state_id, "value": None}
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to reach app: {e}")


@app.post("/api/v1/apps/{app_id}/actions/{action_id}")
async def proxy_invoke_action(
    app_id: str,
    action_id: str,
    request: ActionInvokeRequest,
    x_session_id: Optional[str] = Header(None),
):
    """Proxy action invocation to the app container."""
    app = registry.get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail=f"App not found: {app_id}")

    try:
        headers = {}
        if x_session_id:
            headers["X-Session-Id"] = x_session_id

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{app.container_url}/api/v1/actions/{action_id}",
                json={"parameters": request.parameters, "source": request.source},
                headers=headers,
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to reach app: {e}")


# WebSocket endpoint for real-time registry updates
@app.websocket("/ws/registry")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for real-time registry updates."""
    await websocket.accept()

    session_id = websocket.query_params.get("session_id")
    connection = WebSocketConnection(websocket, session_id)
    registry.add_ws_connection(connection)

    logger.info(f"Registry WebSocket connected: session_id={session_id}")

    # Send current app list
    await connection.send({
        "type": "initial_state",
        "apps": [a.to_dict() for a in registry.list_apps()],
    })

    try:
        while True:
            # Handle incoming messages
            data = await websocket.receive_json()
            message_type = data.get("type")

            if message_type == "ping":
                await connection.send({"type": "pong"})

            elif message_type == "get_apps":
                apps = registry.list_apps(
                    category=data.get("category"),
                    status=data.get("status"),
                )
                await connection.send({
                    "type": "apps_list",
                    "apps": [a.to_dict() for a in apps],
                })

    except WebSocketDisconnect:
        logger.info(f"Registry WebSocket disconnected: session_id={session_id}")
    except Exception as e:
        logger.error(f"Registry WebSocket error: {e}")
    finally:
        registry.remove_ws_connection(connection)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8010"))
    uvicorn.run(app, host="0.0.0.0", port=port)
