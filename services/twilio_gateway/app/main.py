"""
Twilio Gateway Main Application
FastAPI server for handling Twilio phone calls integration
"""

import asyncio
import base64
import json
import logging
from contextlib import asynccontextmanager
from typing import Optional, Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .clients.database_client import DatabaseClient
from .clients.rabbitmq_client import RabbitMQClient

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global clients
db_client: Optional[DatabaseClient] = None
rabbitmq_client: Optional[RabbitMQClient] = None

# Active Twilio media streams (call_sid -> WebSocket)
active_streams: Dict[str, WebSocket] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifecycle manager for FastAPI application
    Handles startup and shutdown of global resources
    """
    global db_client, rabbitmq_client

    logger.info(f"Starting Twilio Gateway v{settings.SERVICE_VERSION}")

    # Initialize clients
    db_client = DatabaseClient()
    rabbitmq_client = RabbitMQClient()

    # Connect to services
    try:
        await db_client.connect()
        await rabbitmq_client.connect()
        logger.info("All services connected successfully")
    except Exception as e:
        logger.error(f"Failed to connect to services: {e}")
        raise

    # Start background task to consume TTS audio for Twilio playback
    tts_consumer_task = asyncio.create_task(consume_twilio_tts())

    yield

    # Shutdown
    logger.info("Shutting down Twilio Gateway")

    # Cancel background tasks
    tts_consumer_task.cancel()

    # Close all active streams
    for call_sid, ws in list(active_streams.items()):
        try:
            await ws.close()
        except Exception:
            pass
    active_streams.clear()

    # Close clients
    await rabbitmq_client.close()
    await db_client.close()

    logger.info("Twilio Gateway shutdown complete")


async def consume_twilio_tts():
    """
    Consume TTS audio from twilio_tts_queue and send to active Twilio streams.
    This handles agent speech being played back to phone callers.
    """
    import aio_pika
    import base64

    try:
        connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
        channel = await connection.channel()

        # Declare queue for TTS audio destined for Twilio
        queue = await channel.declare_queue("twilio_tts_queue", durable=True)

        logger.info("Started consuming from twilio_tts_queue")

        async for message in queue:
            async with message.process():
                try:
                    data = json.loads(message.body.decode())

                    call_sid = data.get("call_sid")
                    stream_sid = data.get("stream_sid")
                    audio_b64 = data.get("audio_data")  # mulaw audio in base64

                    logger.info(f"📞 TTS message received: call_sid={call_sid}, stream_sid={stream_sid}, audio_size={len(audio_b64) if audio_b64 else 0}")
                    logger.info(f"📞 Active streams: {list(active_streams.keys())}")

                    if call_sid and audio_b64 and call_sid in active_streams:
                        ws = active_streams[call_sid]

                        # Decode the mulaw audio
                        mulaw_audio = base64.b64decode(audio_b64)

                        # Twilio expects audio in small chunks (~20ms each)
                        # At 8kHz mulaw (8-bit), 20ms = 160 bytes
                        CHUNK_SIZE = 160
                        chunks_sent = 0

                        for i in range(0, len(mulaw_audio), CHUNK_SIZE):
                            chunk = mulaw_audio[i:i + CHUNK_SIZE]
                            chunk_b64 = base64.b64encode(chunk).decode()

                            media_message = {
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {
                                    "payload": chunk_b64
                                }
                            }

                            await ws.send_json(media_message)
                            chunks_sent += 1

                            # Small delay between chunks to match playback rate
                            # 160 bytes at 8kHz = 20ms of audio
                            await asyncio.sleep(0.015)  # ~15ms to stay ahead

                        logger.info(f"📞🔊 Sent {len(mulaw_audio)} bytes ({chunks_sent} chunks) TTS audio to Twilio stream {call_sid}")
                    else:
                        logger.warning(f"📞 Cannot send TTS: call_sid={call_sid}, has_audio={bool(audio_b64)}, in_active_streams={call_sid in active_streams if call_sid else False}")

                except Exception as e:
                    logger.error(f"Error processing TTS for Twilio: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"Error in Twilio TTS consumer: {e}")


# Create FastAPI app
app = FastAPI(
    title="VOS Twilio Gateway",
    version=settings.SERVICE_VERSION,
    lifespan=lifespan
)

# Add CORS middleware with explicit allowed origins
# Note: Twilio webhooks don't need CORS, but this protects any browser-facing endpoints
ALLOWED_ORIGINS = [
    "https://jarvos.dev",
    "https://app.jarvos.dev",
    "https://api.jarvos.dev",
    "http://localhost:3000",  # Local development
    "http://localhost:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Internal-Key"],
)

# Import and include routers
from .routers import webhooks, media_stream, outbound, sms

app.include_router(webhooks.router, prefix="/twilio", tags=["Twilio Webhooks"])
app.include_router(media_stream.router, prefix="/twilio", tags=["Media Streams"])
app.include_router(outbound.router, prefix="/twilio", tags=["Outbound Calls"])
app.include_router(sms.router, prefix="/twilio", tags=["SMS"])


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": settings.SERVICE_NAME,
        "version": settings.SERVICE_VERSION,
        "active_calls": len(active_streams)
    }


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": settings.SERVICE_NAME,
        "version": settings.SERVICE_VERSION,
        "endpoints": {
            "webhooks": {
                "incoming": "/twilio/voice/incoming",
                "outbound": "/twilio/voice/outbound",
                "status": "/twilio/voice/status"
            },
            "media_stream": "/twilio/media-stream/{session_id}",
            "sms": {
                "send": "/twilio/sms/send",
                "receive": "/twilio/sms/receive"
            },
            "health": "/health"
        }
    }


# For development/testing
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8200,
        reload=True,
        log_level=settings.LOG_LEVEL.lower()
    )
