"""
Transcription router for VOS API Gateway.

Handles batch audio transcription using AssemblyAI.
"""

import logging
import os
import uuid
from pathlib import Path
from typing import Optional
from datetime import datetime
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends
from pydantic import BaseModel
import asyncio
import json

try:
    import assemblyai as aai
except ImportError:
    aai = None
    logging.getLogger(__name__).warning("AssemblyAI package not installed")

# Store active transcription jobs in memory (could be moved to Redis/DB for production)
transcription_jobs = {}

logger = logging.getLogger(__name__)

router = APIRouter()

# Get AssemblyAI API key from environment
ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY", "")

# Audio upload path (shared volume)
UPLOAD_BASE_PATH = Path("/shared/transcription_uploads")
UPLOAD_BASE_PATH.mkdir(parents=True, exist_ok=True)


class TranscriptionJob(BaseModel):
    """Transcription job status"""
    job_id: str
    status: str  # 'pending', 'processing', 'completed', 'failed'
    filename: Optional[str] = None
    error: Optional[str] = None


class TranscriptionResult(BaseModel):
    """Transcription result"""
    job_id: str
    status: str
    text: Optional[str] = None
    confidence: Optional[float] = None
    audio_duration: Optional[float] = None
    utterances: Optional[list] = None
    error: Optional[str] = None


@router.post("/transcription/upload", response_model=TranscriptionJob)
async def upload_audio_for_transcription(
    file: UploadFile = File(..., description="Audio file to transcribe"),
    speech_model: str = Form("universal", description="AssemblyAI speech model"),
    speaker_labels: bool = Form(False, description="Enable speaker diarization"),
    session_id: str = Form("user_session_default", description="User session ID for routing to agent"),
    user_timezone: Optional[str] = Form(None, description="User timezone for context")
):
    """
    Upload an audio file for batch transcription.

    Args:
        file: Audio file (supported formats: mp3, wav, webm, m4a, ogg)
        speech_model: AssemblyAI speech model to use (default: universal)
        speaker_labels: Enable speaker diarization

    Returns:
        TranscriptionJob with job_id to track progress

    Raises:
        HTTPException: 400 if invalid file, 500 if upload fails
    """
    try:
        # Validate file type
        allowed_extensions = {".mp3", ".wav", ".webm", ".m4a", ".ogg", ".flac"}
        file_ext = Path(file.filename).suffix.lower()

        if file_ext not in allowed_extensions:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {file_ext}. Allowed: {allowed_extensions}"
            )

        # Generate job ID
        job_id = str(uuid.uuid4())

        # Save uploaded file
        file_path = UPLOAD_BASE_PATH / f"{job_id}{file_ext}"

        # Read and save file
        contents = await file.read()

        if len(contents) == 0:
            raise HTTPException(status_code=400, detail="Empty file")

        if len(contents) > 100 * 1024 * 1024:  # 100MB limit
            raise HTTPException(status_code=400, detail="File too large (max 100MB)")

        file_path.write_bytes(contents)

        logger.info(f"📤 Uploaded audio file for transcription: {job_id} ({file.filename})")

        # Create job record
        job = {
            "job_id": job_id,
            "status": "pending",
            "filename": file.filename,
            "file_path": str(file_path),
            "speech_model": speech_model,
            "speaker_labels": speaker_labels,
            "session_id": session_id,
            "user_timezone": user_timezone,
            "error": None,
            "result": None
        }

        transcription_jobs[job_id] = job

        # Start transcription in background
        asyncio.create_task(_process_transcription(job_id))

        return TranscriptionJob(
            job_id=job_id,
            status="pending",
            filename=file.filename
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading audio for transcription: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload audio file")


@router.get("/transcription/{job_id}", response_model=TranscriptionJob)
async def get_transcription_status(job_id: str):
    """
    Get transcription job status.

    Args:
        job_id: Transcription job ID

    Returns:
        TranscriptionJob with current status

    Raises:
        HTTPException: 404 if job not found
    """
    if job_id not in transcription_jobs:
        raise HTTPException(status_code=404, detail="Transcription job not found")

    job = transcription_jobs[job_id]

    return TranscriptionJob(
        job_id=job_id,
        status=job["status"],
        filename=job.get("filename"),
        error=job.get("error")
    )


@router.get("/transcription/{job_id}/result", response_model=TranscriptionResult)
async def get_transcription_result(job_id: str):
    """
    Get transcription result for completed job.

    Args:
        job_id: Transcription job ID

    Returns:
        TranscriptionResult with transcribed text

    Raises:
        HTTPException: 404 if job not found, 425 if not completed
    """
    if job_id not in transcription_jobs:
        raise HTTPException(status_code=404, detail="Transcription job not found")

    job = transcription_jobs[job_id]

    if job["status"] == "pending" or job["status"] == "processing":
        raise HTTPException(
            status_code=425,
            detail=f"Transcription not ready yet (status: {job['status']})"
        )

    if job["status"] == "failed":
        return TranscriptionResult(
            job_id=job_id,
            status="failed",
            error=job.get("error", "Transcription failed")
        )

    # Return result
    result = job.get("result", {})

    return TranscriptionResult(
        job_id=job_id,
        status="completed",
        text=result.get("text"),
        confidence=result.get("confidence"),
        audio_duration=result.get("audio_duration"),
        utterances=result.get("utterances")
    )


async def _process_transcription(job_id: str):
    """
    Background task to process transcription.

    Args:
        job_id: Transcription job ID
    """
    try:
        job = transcription_jobs[job_id]
        job["status"] = "processing"

        logger.info(f"🎙️ Starting transcription for job {job_id}")

        if not aai:
            job["status"] = "failed"
            job["error"] = "AssemblyAI package not installed"
            return

        if not ASSEMBLYAI_API_KEY:
            job["status"] = "failed"
            job["error"] = "AssemblyAI API key not configured"
            return

        try:
            # Configure AssemblyAI
            aai.settings.api_key = ASSEMBLYAI_API_KEY

            # Set up transcription config
            config = aai.TranscriptionConfig(
                speech_model=aai.SpeechModel.best if job["speech_model"] == "best" else aai.SpeechModel.nano,
                speaker_labels=job["speaker_labels"]
            )

            # Run transcription in executor (it's synchronous)
            transcriber = aai.Transcriber(config=config)
            transcript = await asyncio.get_event_loop().run_in_executor(
                None,
                transcriber.transcribe,
                job["file_path"]
            )

            if transcript.status == aai.TranscriptStatus.error:
                raise RuntimeError(f"Transcription failed: {transcript.error}")

            # Build result
            result = {
                "status": "completed",
                "text": transcript.text,
                "confidence": getattr(transcript, "confidence", None),
                "audio_duration": getattr(transcript, "audio_duration", None),
            }

            # Add speaker labels if requested
            if job["speaker_labels"] and hasattr(transcript, "utterances") and transcript.utterances:
                result["utterances"] = [
                    {
                        "speaker": u.speaker,
                        "text": u.text,
                        "start": u.start,
                        "end": u.end,
                        "confidence": u.confidence
                    }
                    for u in transcript.utterances
                ]

            # Store result
            job["result"] = result
            job["status"] = "completed"

            logger.info(f"✅ Transcription completed for job {job_id}")

            # Store in conversation history
            try:
                from app.main import db_client

                insert_query = """
                INSERT INTO conversation_messages (session_id, sender_type, sender_id, content, metadata, input_mode)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, timestamp;
                """

                metadata = {
                    "content_type": "voice_transcript",
                    "job_id": job_id,
                    "confidence": result.get("confidence"),
                    "audio_duration": result.get("audio_duration"),
                    "filename": job.get("filename"),
                    "source": "batch_transcription"
                }

                db_result = db_client.execute_query(
                    insert_query,
                    (
                        job.get("session_id", "user_session_default"),
                        "user",
                        "user",
                        result.get("text", ""),
                        json.dumps(metadata),
                        "voice"
                    )
                )

                if db_result:
                    logger.info(f"📝 Stored voice message in conversation history: {db_result[0][0]}")

            except Exception as e:
                logger.error(f"Error storing voice message in database: {e}")

            # Send transcription to primary agent
            try:
                from app.main import rabbitmq_client

                notification = {
                    "notification_id": str(uuid.uuid4()),
                    "timestamp": datetime.utcnow().isoformat(),
                    "recipient_agent_id": "primary_agent",
                    "notification_type": "user_message",
                    "source": "batch_transcription",
                    "payload": {
                        "content": result.get("text", ""),
                        "content_type": "voice_transcript",
                        "session_id": job.get("session_id", "user_session_default"),
                        "user_timezone": job.get("user_timezone"),
                        "voice_metadata": {
                            "job_id": job_id,
                            "confidence": result.get("confidence"),
                            "audio_duration": result.get("audio_duration"),
                            "filename": job.get("filename"),
                            "source": "batch_transcription"
                        }
                    }
                }

                success = rabbitmq_client.publish_message("primary_agent_queue", notification)
                if success:
                    logger.info(f"📨 Published transcription {job_id} to primary_agent_queue")
                else:
                    logger.error(f"Failed to publish transcription {job_id} to primary_agent_queue")

            except Exception as e:
                logger.error(f"Error publishing transcription to agent: {e}")

        except Exception as e:
            logger.error(f"AssemblyAI transcription error: {e}")
            job["status"] = "failed"
            job["error"] = str(e)

    except Exception as e:
        logger.error(f"Error processing transcription {job_id}: {e}")
        job["status"] = "failed"
        job["error"] = str(e)
