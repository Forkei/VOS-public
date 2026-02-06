"""
Note Tools

Implements tools for managing notes with Google Cloud Storage integration.
"""

from vos_sdk.tools.base import BaseTool
from typing import Dict, Any, List, Optional
from datetime import datetime
import logging
import requests
import os
import json
import psycopg2
import psycopg2.extras
from google.cloud import storage
from google.oauth2 import service_account
import base64

logger = logging.getLogger(__name__)

# GCS Configuration
GCS_BUCKET_NAME = os.getenv('GCS_BUCKET_NAME', 'vos-notes-storage')
GCS_CREDENTIALS_JSON = os.getenv('GCS_CREDENTIALS_JSON', '')
GCS_PROJECT_ID = os.getenv('GCS_PROJECT_ID', '')

# Storage threshold - content larger than this will be stored in GCS
GCS_STORAGE_THRESHOLD = int(os.getenv('GCS_STORAGE_THRESHOLD', '100000'))  # 100KB default


class NotesDatabaseClient:
    """Database client for notes tools."""
    def __init__(self):
        self.conn = psycopg2.connect(
            host=os.getenv('DATABASE_HOST', 'postgres'),
            port=os.getenv('DATABASE_PORT', '5432'),
            database=os.getenv('DATABASE_NAME', 'vos_database'),
            user=os.getenv('DATABASE_USER', 'vos_user'),
            password=os.getenv('DATABASE_PASSWORD', 'vos_password')
        )

    def execute_query(self, query, params=None, fetch=True):
        """Execute a query and optionally fetch results."""
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            if fetch:
                return cur.fetchall()

    def commit(self):
        """Commit the transaction."""
        self.conn.commit()

    def close(self):
        """Close the connection."""
        self.conn.close()


class GCSStorageClient:
    """Google Cloud Storage client for managing large note content."""

    def __init__(self):
        self.client = None
        self.bucket = None
        self._initialize_client()

    def _initialize_client(self):
        """Initialize GCS client with credentials."""
        try:
            if GCS_CREDENTIALS_JSON:
                # Credentials provided as JSON string
                credentials_dict = json.loads(GCS_CREDENTIALS_JSON)
                credentials = service_account.Credentials.from_service_account_info(credentials_dict)
                self.client = storage.Client(credentials=credentials, project=GCS_PROJECT_ID)
            else:
                # Use default credentials (application default credentials)
                self.client = storage.Client(project=GCS_PROJECT_ID)

            self.bucket = self.client.bucket(GCS_BUCKET_NAME)
            logger.info(f"✅ GCS client initialized for bucket: {GCS_BUCKET_NAME}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to initialize GCS client: {e}. Large notes will be stored in database.")
            self.client = None
            self.bucket = None

    def is_available(self) -> bool:
        """Check if GCS is available for use."""
        return self.client is not None and self.bucket is not None

    def upload_content(self, note_id: int, content: str, content_type: str = 'text/plain') -> tuple[str, str]:
        """
        Upload note content to GCS.

        Returns:
            tuple: (gcs_bucket, gcs_path)
        """
        if not self.is_available():
            raise ValueError("GCS is not available. Check credentials and configuration.")

        # Create path: notes/{note_id}/{timestamp}.txt
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        gcs_path = f"notes/{note_id}/{timestamp}.txt"

        blob = self.bucket.blob(gcs_path)
        blob.upload_from_string(content, content_type=content_type)

        logger.info(f"✅ Uploaded content to GCS: {gcs_path}")
        return GCS_BUCKET_NAME, gcs_path

    def download_content(self, gcs_path: str) -> str:
        """Download note content from GCS."""
        if not self.is_available():
            raise ValueError("GCS is not available. Check credentials and configuration.")

        blob = self.bucket.blob(gcs_path)
        content = blob.download_as_text()
        return content

    def delete_content(self, gcs_path: str):
        """Delete note content from GCS."""
        if not self.is_available():
            return

        try:
            blob = self.bucket.blob(gcs_path)
            blob.delete()
            logger.info(f"🗑️ Deleted content from GCS: {gcs_path}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to delete GCS content: {e}")


def publish_app_interaction(agent_id: str, app_name: str, action: str, result: dict, session_id: str = None):
    """Publish notification to frontend via API Gateway"""
    api_gateway_url = os.getenv('API_GATEWAY_URL', 'http://api_gateway:8000')
    url = f"{api_gateway_url}/api/v1/notifications/app-interaction"

    internal_api_key = os.getenv('INTERNAL_API_KEY', '')

    payload = {
        'agent_id': agent_id,
        'app_name': app_name,
        'action': action,
        'result': result,
        'session_id': session_id
    }

    headers = {
        'Content-Type': 'application/json',
        'X-Internal-Key': internal_api_key
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=5)
        response.raise_for_status()
    except Exception as e:
        logger.warning(f"Failed to publish app interaction: {e}")


class CreateNoteTool(BaseTool):
    """Create a new note with optional GCS storage for large content."""

    def __init__(self):
        super().__init__(
            name="create_note",
            description="Creates a new note with title, content, tags, and folder organization. Large content is automatically stored in Google Cloud Storage."
        )

    def execute(self, context: Dict[str, Any]) -> None:
        db = None
        gcs_client = GCSStorageClient()

        try:
            title = context['title']
            content = context['content']
            tags = context.get('tags', [])
            folder = context.get('folder')
            color = context.get('color')
            content_type = context.get('content_type', 'text/plain')
            is_pinned = context.get('is_pinned', False)
            created_by = context.get('created_by', 'user')  # Default to 'user' if not provided

            db = NotesDatabaseClient()

            # Determine if content should be stored in GCS
            content_length = len(content)
            use_gcs = content_length > GCS_STORAGE_THRESHOLD and gcs_client.is_available()

            gcs_bucket = None
            gcs_path = None
            db_content = content  # Store in DB by default

            if use_gcs:
                # Create a placeholder record first to get the note ID
                query = """
                    INSERT INTO notes (title, content, tags, folder, color, content_type,
                                     content_length, is_pinned, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, created_at, updated_at
                """
                result = db.execute_query(
                    query,
                    (title, '[Content stored in GCS]', tags, folder, color, content_type,
                     content_length, is_pinned, created_by)
                )
                note_id = result[0]['id']
                created_at = result[0]['created_at']
                updated_at = result[0]['updated_at']

                # Upload to GCS
                gcs_bucket, gcs_path = gcs_client.upload_content(note_id, content, content_type)

                # Update with GCS reference
                update_query = """
                    UPDATE notes
                    SET gcs_bucket = %s, gcs_path = %s
                    WHERE id = %s
                """
                db.execute_query(update_query, (gcs_bucket, gcs_path, note_id), fetch=False)
                db.commit()

                logger.info(f"✅ Created note #{note_id} with GCS storage")
            else:
                # Store content directly in database
                query = """
                    INSERT INTO notes (title, content, tags, folder, color, content_type,
                                     content_length, is_pinned, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, created_at, updated_at
                """
                result = db.execute_query(
                    query,
                    (title, content, tags, folder, color, content_type,
                     content_length, is_pinned, created_by)
                )
                note_id = result[0]['id']
                created_at = result[0]['created_at']
                updated_at = result[0]['updated_at']
                db.commit()

                logger.info(f"✅ Created note #{note_id} with database storage")

            # Create content preview (first 500 chars)
            content_preview = content[:500] if len(content) > 500 else content

            note_data = {
                'id': note_id,
                'title': title,
                'content': content,
                'content_preview': content_preview,
                'tags': tags,
                'folder': folder,
                'color': color,
                'content_type': content_type,
                'content_length': content_length,
                'is_pinned': is_pinned,
                'is_archived': False,  # New notes are never archived
                'has_gcs_content': use_gcs,
                'storage_location': 'gcs' if use_gcs else 'database',
                'created_by': created_by,
                'created_at': created_at.isoformat() if created_at else None,
                'updated_at': updated_at.isoformat() if updated_at else None
            }

            # Publish app interaction
            publish_app_interaction(
                agent_id='notes_agent',
                app_name='notes',
                action='note_created',
                result=note_data,
                session_id=context.get('session_id')
            )

            # Send success notification
            self.send_result_notification(
                status="SUCCESS",
                result=note_data
            )

        except Exception as e:
            logger.error(f"❌ Error creating note: {e}", exc_info=True)
            # Send failure notification
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to create note: {e}"
            )
        finally:
            if db:
                db.close()

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "create_note",
            "description": "Creates a new note with title, content, tags, and folder organization. Large content is automatically stored in Google Cloud Storage.",
            "parameters": [
                {
                    "name": "title",
                    "type": "str",
                    "description": "The title of the note",
                    "required": True
                },
                {
                    "name": "content",
                    "type": "str",
                    "description": "The content/body of the note",
                    "required": True
                },
                {
                    "name": "tags",
                    "type": "list",
                    "description": "Optional tags for organizing the note",
                    "required": False
                },
                {
                    "name": "folder",
                    "type": "str",
                    "description": "Optional folder path for organization",
                    "required": False
                },
                {
                    "name": "color",
                    "type": "str",
                    "description": "Optional color code for visual categorization",
                    "required": False
                },
                {
                    "name": "content_type",
                    "type": "str",
                    "description": "Content MIME type (default: text/plain)",
                    "required": False
                },
                {
                    "name": "is_pinned",
                    "type": "bool",
                    "description": "Whether to pin the note",
                    "required": False
                },
                {
                    "name": "created_by",
                    "type": "str",
                    "description": "User identifier who created the note (optional, defaults to 'user')",
                    "required": False
                }
            ]
        }


class ListNotesTool(BaseTool):
    """List notes with filtering and pagination."""

    def __init__(self):
        super().__init__(
            name="list_notes",
            description="Lists notes with optional filtering by folder, tags, pinned status, and archived status. Supports pagination."
        )

    def execute(self, context: Dict[str, Any]) -> None:
        db = None

        try:
            folder = context.get('folder')
            tags = context.get('tags', [])
            is_pinned = context.get('is_pinned')
            is_archived = context.get('is_archived', False)
            created_by = context.get('created_by')  # Optional - if empty, returns all notes
            limit = context.get('limit', 50)
            offset = context.get('offset', 0)
            sort_by = context.get('sort_by', 'updated_at')
            sort_order = context.get('sort_order', 'desc').upper()

            # Validate sort parameters
            valid_sort_fields = ['created_at', 'updated_at', 'title']
            if sort_by not in valid_sort_fields:
                sort_by = 'updated_at'
            if sort_order not in ['ASC', 'DESC']:
                sort_order = 'DESC'

            db = NotesDatabaseClient()

            # Build query with filters
            conditions = ['is_archived = %s']
            params = [is_archived]

            # Only filter by created_by if provided
            if created_by:
                conditions.append('created_by = %s')
                params.append(created_by)

            if folder is not None:
                conditions.append('folder = %s')
                params.append(folder)

            if tags:
                conditions.append('tags && %s')
                params.append(tags)

            if is_pinned is not None:
                conditions.append('is_pinned = %s')
                params.append(is_pinned)

            where_clause = ' AND '.join(conditions)

            # Get total count
            count_query = f"SELECT COUNT(*) as total FROM notes WHERE {where_clause}"
            count_result = db.execute_query(count_query, params)
            total_count = count_result[0]['total']

            # Get notes (excluding large content stored in GCS)
            query = f"""
                SELECT id, title,
                       CASE
                           WHEN gcs_path IS NOT NULL THEN '[Content stored in GCS - use get_note to retrieve]'
                           ELSE LEFT(content, 500)
                       END as content_preview,
                       tags, folder, color, content_type, content_length,
                       is_pinned, is_archived, gcs_bucket, gcs_path,
                       created_by, created_at, updated_at
                FROM notes
                WHERE {where_clause}
                ORDER BY {sort_by} {sort_order}
                LIMIT %s OFFSET %s
            """
            params.extend([limit, offset])

            notes = db.execute_query(query, params)

            # Convert to dict list
            notes_list = []
            for note in notes:
                notes_list.append({
                    'id': note['id'],
                    'title': note['title'],
                    'content_preview': note['content_preview'],
                    'tags': note['tags'] or [],
                    'folder': note['folder'],
                    'color': note['color'],
                    'content_type': note['content_type'],
                    'content_length': note['content_length'],
                    'is_pinned': note['is_pinned'],
                    'is_archived': note['is_archived'],
                    'has_gcs_content': note['gcs_path'] is not None,
                    'created_by': note['created_by'],
                    'created_at': note['created_at'].isoformat() if note['created_at'] else None,
                    'updated_at': note['updated_at'].isoformat() if note['updated_at'] else None
                })

            result_data = {
                'notes': notes_list,
                'total_count': total_count,
                'limit': limit,
                'offset': offset,
                'has_more': (offset + limit) < total_count
            }

            self.send_result_notification(
                status="SUCCESS",
                result=result_data
            )

        except Exception as e:
            logger.error(f"❌ Error listing notes: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to list notes: {e}"
            )
        finally:
            if db:
                db.close()

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "list_notes",
            "description": "Lists notes with optional filtering by folder, tags, pinned status, and archived status. Supports pagination.",
            "parameters": [
                {"name": "folder", "type": "str", "description": "Filter by folder name", "required": False},
                {"name": "tags", "type": "list", "description": "Filter by tags", "required": False},
                {"name": "is_pinned", "type": "bool", "description": "Filter by pinned status", "required": False},
                {"name": "is_archived", "type": "bool", "description": "Filter by archived status", "required": False},
                {"name": "created_by", "type": "str", "description": "Filter by creator", "required": False},
                {"name": "limit", "type": "int", "description": "Maximum notes to return (default: 50)", "required": False},
                {"name": "offset", "type": "int", "description": "Pagination offset (default: 0)", "required": False},
                {"name": "sort_by", "type": "str", "description": "Sort field (default: updated_at)", "required": False},
                {"name": "sort_order", "type": "str", "description": "Sort order: asc/desc (default: desc)", "required": False}
            ]
        }


class GetNoteTool(BaseTool):
    """Get a specific note by ID, including full content from GCS if applicable."""

    def __init__(self):
        super().__init__(
            name="get_note",
            description="Retrieves a specific note by ID, including full content. Automatically fetches content from Google Cloud Storage if needed."
        )

    def execute(self, context: Dict[str, Any]) -> None:
        db = None
        gcs_client = GCSStorageClient()

        try:
            note_id = context['note_id']
            created_by = context.get('created_by')  # Optional

            db = NotesDatabaseClient()

            if created_by:
                query = """
                    SELECT * FROM notes
                    WHERE id = %s AND created_by = %s
                """
                result = db.execute_query(query, (note_id, created_by))
            else:
                query = """
                    SELECT * FROM notes
                    WHERE id = %s
                """
                result = db.execute_query(query, (note_id,))

            if not result:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Note {note_id} not found or access denied"
                )
                return

            note = result[0]

            # Fetch content from GCS if needed
            content = note['content']
            if note['gcs_path'] and gcs_client.is_available():
                try:
                    content = gcs_client.download_content(note['gcs_path'])
                except Exception as e:
                    logger.error(f"Failed to fetch content from GCS: {e}")
                    content = f"[Error retrieving content from GCS: {e}]"

            note_data = {
                'id': note['id'],
                'title': note['title'],
                'tags': note['tags'] or [],
                'folder': note['folder'],
                'color': note['color'],
                'content_type': note['content_type'],
                'content_length': note['content_length'],
                'is_pinned': note['is_pinned'],
                'is_archived': note['is_archived'],
                'gcs_bucket': note['gcs_bucket'],
                'gcs_path': note['gcs_path'],
                'created_by': note['created_by'],
                'created_at': note['created_at'].isoformat() if note['created_at'] else None,
                'updated_at': note['updated_at'].isoformat() if note['updated_at'] else None,
                'storage_location': 'gcs' if note['gcs_path'] else 'database'
            }

            # Send full content to frontend via app_interaction
            frontend_data = {
                **note_data,
                'content': content  # Full content from GCS or database
            }
            publish_app_interaction(
                agent_id='notes_agent',
                app_name='notes',
                action='note_viewed',
                result=frontend_data,
                session_id=context.get('session_id')
            )

            # Send to agent without full content (keeps context small)
            self.send_result_notification(
                status="SUCCESS",
                result=note_data
            )

        except Exception as e:
            logger.error(f"❌ Error getting note: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to get note: {e}"
            )
        finally:
            if db:
                db.close()

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "get_note",
            "description": "Retrieves a specific note by ID, including full content. Automatically fetches content from Google Cloud Storage if needed.",
            "parameters": [
                {"name": "note_id", "type": "int", "description": "ID of the note to retrieve", "required": True}
            ]
        }


class UpdateNoteTool(BaseTool):
    """Update an existing note."""

    def __init__(self):
        super().__init__(
            name="update_note",
            description="Updates an existing note. Can update title, content, tags, folder, color, and pinned status. Large content is automatically stored in GCS."
        )

    def execute(self, context: Dict[str, Any]) -> None:
        db = None
        gcs_client = GCSStorageClient()

        try:
            note_id = context['note_id']
            created_by = context['created_by']

            db = NotesDatabaseClient()

            # Check note exists and belongs to user
            check_query = "SELECT * FROM notes WHERE id = %s AND created_by = %s"
            existing = db.execute_query(check_query, (note_id, created_by))

            if not existing:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Note {note_id} not found or access denied"
                )
                return

            existing_note = existing[0]

            # Build update query dynamically
            updates = []
            params = []

            if 'title' in context and context['title'] is not None:
                updates.append('title = %s')
                params.append(context['title'])

            if 'content' in context and context['content'] is not None:
                new_content = context['content']
                content_length = len(new_content)
                use_gcs = content_length > GCS_STORAGE_THRESHOLD and gcs_client.is_available()

                if use_gcs:
                    # Upload to GCS
                    gcs_bucket, gcs_path = gcs_client.upload_content(
                        note_id, new_content, existing_note['content_type']
                    )

                    # Delete old GCS content if exists
                    if existing_note['gcs_path']:
                        gcs_client.delete_content(existing_note['gcs_path'])

                    updates.extend(['content = %s', 'gcs_bucket = %s', 'gcs_path = %s', 'content_length = %s'])
                    params.extend(['[Content stored in GCS]', gcs_bucket, gcs_path, content_length])
                else:
                    # Delete old GCS content if exists
                    if existing_note['gcs_path']:
                        gcs_client.delete_content(existing_note['gcs_path'])

                    updates.extend(['content = %s', 'gcs_bucket = NULL', 'gcs_path = NULL', 'content_length = %s'])
                    params.extend([new_content, content_length])

            if 'tags' in context and context['tags'] is not None:
                updates.append('tags = %s')
                params.append(context['tags'])

            if 'folder' in context and context['folder'] is not None:
                updates.append('folder = %s')
                params.append(context['folder'])

            if 'color' in context and context['color'] is not None:
                updates.append('color = %s')
                params.append(context['color'])

            if 'is_pinned' in context and context['is_pinned'] is not None:
                updates.append('is_pinned = %s')
                params.append(context['is_pinned'])

            if not updates:
                self.send_result_notification(
                    status="FAILURE",
                    error_message="No fields to update were provided"
                )
                return

            # Execute update
            updates.append('updated_at = NOW()')
            update_query = f"""
                UPDATE notes
                SET {', '.join(updates)}
                WHERE id = %s AND created_by = %s
                RETURNING id, title, content, tags, folder, color, content_type,
                         content_length, is_pinned, is_archived, gcs_bucket, gcs_path,
                         created_by, created_at, updated_at
            """
            params.extend([note_id, created_by])

            result = db.execute_query(update_query, params)
            db.commit()

            updated_note = result[0]

            # Get content - if it was just updated, use new_content; otherwise use DB value
            if 'content' in context and context['content'] is not None:
                content = context['content']
            elif updated_note['gcs_path'] and gcs_client.is_available():
                # Fetch from GCS
                try:
                    content = gcs_client.download_content(updated_note['gcs_path'])
                except Exception as e:
                    content = updated_note['content']
            else:
                content = updated_note['content']

            # Create content preview
            content_preview = content[:500] if content and len(content) > 500 else content

            note_data = {
                'id': updated_note['id'],
                'title': updated_note['title'],
                'content': content,
                'content_preview': content_preview,
                'tags': updated_note['tags'] or [],
                'folder': updated_note['folder'],
                'color': updated_note['color'],
                'content_type': updated_note['content_type'],
                'content_length': updated_note['content_length'],
                'is_pinned': updated_note['is_pinned'],
                'is_archived': updated_note['is_archived'],
                'has_gcs_content': updated_note['gcs_path'] is not None,
                'storage_location': 'gcs' if updated_note['gcs_path'] else 'database',
                'created_by': updated_note['created_by'],
                'created_at': updated_note['created_at'].isoformat() if updated_note['created_at'] else None,
                'updated_at': updated_note['updated_at'].isoformat() if updated_note['updated_at'] else None
            }

            # Publish app interaction
            publish_app_interaction(
                agent_id='notes_agent',
                app_name='notes',
                action='note_updated',
                result=note_data,
                session_id=context.get('session_id')
            )

            self.send_result_notification(
                status="SUCCESS",
                result=note_data
            )

        except Exception as e:
            logger.error(f"❌ Error updating note: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to update note: {e}"
            )
        finally:
            if db:
                db.close()

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "update_note",
            "description": "Updates an existing note. Can update title, content, tags, folder, color, and pinned status. Large content is automatically stored in GCS.",
            "parameters": [
                {"name": "note_id", "type": "int", "description": "ID of the note to update", "required": True},
                {"name": "title", "type": "str", "description": "New title", "required": False},
                {"name": "content", "type": "str", "description": "New content", "required": False},
                {"name": "tags", "type": "list", "description": "New tags", "required": False},
                {"name": "folder", "type": "str", "description": "New folder", "required": False},
                {"name": "color", "type": "str", "description": "New color", "required": False},
                {"name": "is_pinned", "type": "bool", "description": "Pin status", "required": False}
            ]
        }


class DeleteNoteTool(BaseTool):
    """Delete a note permanently."""

    def __init__(self):
        super().__init__(
            name="delete_note",
            description="Permanently deletes a note. This action cannot be undone. Also deletes content from GCS if stored there."
        )

    def execute(self, context: Dict[str, Any]) -> None:
        db = None
        gcs_client = GCSStorageClient()

        try:
            note_id = context['note_id']
            created_by = context.get('created_by')  # Optional

            db = NotesDatabaseClient()

            # Get note to check GCS path
            if created_by:
                query = "SELECT title, gcs_path FROM notes WHERE id = %s AND created_by = %s"
                result = db.execute_query(query, (note_id, created_by))
            else:
                query = "SELECT title, gcs_path FROM notes WHERE id = %s"
                result = db.execute_query(query, (note_id,))

            if not result:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Note {note_id} not found or access denied"
                )
                return

            note = result[0]
            title = note['title']
            gcs_path = note['gcs_path']

            # Delete from GCS if exists
            if gcs_path and gcs_client.is_available():
                gcs_client.delete_content(gcs_path)

            # Delete from database
            if created_by:
                delete_query = "DELETE FROM notes WHERE id = %s AND created_by = %s"
                db.execute_query(delete_query, (note_id, created_by), fetch=False)
            else:
                delete_query = "DELETE FROM notes WHERE id = %s"
                db.execute_query(delete_query, (note_id,), fetch=False)
            db.commit()

            # Publish app interaction
            publish_app_interaction(
                agent_id='notes_agent',
                app_name='notes',
                action='note_deleted',
                result={'id': note_id},
                session_id=context.get('session_id')
            )

            self.send_result_notification(
                status="SUCCESS",
                result={'id': note_id, 'title': title}
            )

        except Exception as e:
            logger.error(f"❌ Error deleting note: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to delete note: {e}"
            )
        finally:
            if db:
                db.close()

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "delete_note",
            "description": "Permanently deletes a note. This action cannot be undone. Also deletes content from GCS if stored there.",
            "parameters": [
                {"name": "note_id", "type": "int", "description": "ID of the note to delete", "required": True},
                {"name": "created_by", "type": "str", "description": "User identifier (optional, for access control)", "required": False}
            ]
        }


class SearchNotesTool(BaseTool):
    """Search notes by title and content using full-text search."""

    def __init__(self):
        super().__init__(
            name="search_notes",
            description="Searches notes using full-text search on title and content. Returns matching notes ranked by relevance."
        )

    def execute(self, context: Dict[str, Any]) -> None:
        db = None

        try:
            query = context['query']
            created_by = context['created_by']
            folder = context.get('folder')
            tags = context.get('tags', [])
            limit = context.get('limit', 20)

            db = NotesDatabaseClient()

            # Build search query using PostgreSQL full-text search
            conditions = ['created_by = %s', 'is_archived = false']
            params = [created_by]

            # Full-text search condition
            search_condition = """
                (to_tsvector('english', title) @@ plainto_tsquery('english', %s) OR
                 to_tsvector('english', content) @@ plainto_tsquery('english', %s))
            """
            conditions.append(search_condition)
            params.extend([query, query])

            if folder:
                conditions.append('folder = %s')
                params.append(folder)

            if tags:
                conditions.append('tags && %s')
                params.append(tags)

            where_clause = ' AND '.join(conditions)

            # Search with relevance ranking
            search_query = f"""
                SELECT id, title,
                       CASE
                           WHEN gcs_path IS NOT NULL THEN '[Content stored in GCS]'
                           ELSE LEFT(content, 300)
                       END as content_preview,
                       tags, folder, color, content_type, content_length,
                       is_pinned, gcs_path,
                       created_at, updated_at,
                       ts_rank(to_tsvector('english', title || ' ' || COALESCE(content, '')),
                               plainto_tsquery('english', %s)) as rank
                FROM notes
                WHERE {where_clause}
                ORDER BY rank DESC, updated_at DESC
                LIMIT %s
            """
            params.extend([query, limit])

            results = db.execute_query(search_query, params)

            notes_list = []
            for note in results:
                notes_list.append({
                    'id': note['id'],
                    'title': note['title'],
                    'content_preview': note['content_preview'],
                    'tags': note['tags'] or [],
                    'folder': note['folder'],
                    'color': note['color'],
                    'content_type': note['content_type'],
                    'content_length': note['content_length'],
                    'is_pinned': note['is_pinned'],
                    'has_gcs_content': note['gcs_path'] is not None,
                    'relevance_score': float(note['rank']),
                    'created_at': note['created_at'].isoformat() if note['created_at'] else None,
                    'updated_at': note['updated_at'].isoformat() if note['updated_at'] else None
                })

            self.send_result_notification(
                status="SUCCESS",
                result={'notes': notes_list, 'query': query, 'count': len(notes_list)}
            )

        except Exception as e:
            logger.error(f"❌ Error searching notes: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to search notes: {e}"
            )
        finally:
            if db:
                db.close()

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "search_notes",
            "description": "Searches notes using full-text search on title and content. Returns matching notes ranked by relevance.",
            "parameters": [
                {"name": "query", "type": "str", "description": "Search query text", "required": True},
                {"name": "created_by", "type": "str", "description": "Filter by creator", "required": False},
                {"name": "limit", "type": "int", "description": "Maximum notes to return (default: 20)", "required": False}
            ]
        }


class ArchiveNoteTool(BaseTool):
    """Archive or unarchive a note."""

    def __init__(self):
        super().__init__(
            name="archive_note",
            description="Archives or unarchives a note. Archived notes are hidden from default views but not deleted."
        )

    def execute(self, context: Dict[str, Any]) -> None:
        db = None

        try:
            note_id = context['note_id']
            is_archived = context['is_archived']
            created_by = context['created_by']

            db = NotesDatabaseClient()

            query = """
                UPDATE notes
                SET is_archived = %s, updated_at = NOW()
                WHERE id = %s AND created_by = %s
                RETURNING id, title, is_archived
            """
            result = db.execute_query(query, (is_archived, note_id, created_by))

            if not result:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Note {note_id} not found or access denied"
                )
                return

            db.commit()

            note = result[0]
            action = 'archived' if is_archived else 'unarchived'

            # Publish app interaction
            publish_app_interaction(
                agent_id='notes_agent',
                app_name='notes',
                action=f'note_{action}',
                result={'id': note['id'], 'title': note['title'], 'is_archived': is_archived},
                session_id=context.get('session_id')
            )

            self.send_result_notification(
                status="SUCCESS",
                result={'id': note['id'], 'title': note['title'], 'is_archived': is_archived}
            )

        except Exception as e:
            logger.error(f"❌ Error archiving note: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to archive note: {e}"
            )
        finally:
            if db:
                db.close()

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "archive_note",
            "description": "Archives or unarchives a note. Archived notes are hidden from default views but not deleted.",
            "parameters": [
                {"name": "note_id", "type": "int", "description": "ID of the note to archive/unarchive", "required": True},
                {"name": "is_archived", "type": "bool", "description": "True to archive, False to unarchive", "required": True}
            ]
        }


class PinNoteTool(BaseTool):
    """Pin or unpin a note."""

    def __init__(self):
        super().__init__(
            name="pin_note",
            description="Pins or unpins a note. Pinned notes appear at the top of note lists."
        )

    def execute(self, context: Dict[str, Any]) -> None:
        db = None

        try:
            note_id = context['note_id']
            is_pinned = context['is_pinned']
            created_by = context['created_by']

            db = NotesDatabaseClient()

            query = """
                UPDATE notes
                SET is_pinned = %s, updated_at = NOW()
                WHERE id = %s AND created_by = %s
                RETURNING id, title, content, tags, folder, color, content_type,
                         content_length, is_pinned, is_archived, gcs_bucket, gcs_path,
                         created_by, created_at, updated_at
            """
            result = db.execute_query(query, (is_pinned, note_id, created_by))

            if not result:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Note {note_id} not found or access denied"
                )
                return

            db.commit()

            note = result[0]

            # Get content from GCS if stored there
            gcs_client = GCSStorageClient()
            if note['gcs_path'] and gcs_client.is_available():
                try:
                    content = gcs_client.download_content(note['gcs_path'])
                except Exception as e:
                    content = note['content']
            else:
                content = note['content']

            # Create content preview
            content_preview = content[:500] if content and len(content) > 500 else content

            note_data = {
                'id': note['id'],
                'title': note['title'],
                'content': content,
                'content_preview': content_preview,
                'tags': note['tags'] or [],
                'folder': note['folder'],
                'color': note['color'],
                'content_type': note['content_type'],
                'content_length': note['content_length'],
                'is_pinned': note['is_pinned'],
                'is_archived': note['is_archived'],
                'has_gcs_content': note['gcs_path'] is not None,
                'storage_location': 'gcs' if note['gcs_path'] else 'database',
                'created_by': note['created_by'],
                'created_at': note['created_at'].isoformat() if note['created_at'] else None,
                'updated_at': note['updated_at'].isoformat() if note['updated_at'] else None
            }

            # Publish app interaction as note_updated
            publish_app_interaction(
                agent_id='notes_agent',
                app_name='notes',
                action='note_updated',
                result=note_data,
                session_id=context.get('session_id')
            )

            self.send_result_notification(
                status="SUCCESS",
                result={'id': note['id'], 'title': note['title'], 'is_pinned': is_pinned}
            )

        except Exception as e:
            logger.error(f"❌ Error pinning note: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to pin note: {e}"
            )
        finally:
            if db:
                db.close()

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "pin_note",
            "description": "Pins or unpins a note. Pinned notes appear at the top of note lists.",
            "parameters": [
                {"name": "note_id", "type": "int", "description": "ID of the note to pin/unpin", "required": True},
                {"name": "is_pinned", "type": "bool", "description": "True to pin, False to unpin", "required": True}
            ]
        }
