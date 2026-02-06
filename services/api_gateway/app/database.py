import logging
import os
from contextlib import contextmanager
from typing import List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool
from psycopg2 import sql

from app.schemas import Task, TaskCreate, TaskUpdate

logger = logging.getLogger(__name__)


class DatabaseClient:
    """PostgreSQL database client for task management with support for tasks and task_assignees tables."""
    
    def __init__(self, database_url: str, min_connections: int = 1, max_connections: int = 10):
        """
        Initialize database client with connection pool.
        
        Args:
            database_url: PostgreSQL connection URL
            min_connections: Minimum connections in pool
            max_connections: Maximum connections in pool
        """
        self.database_url = database_url
        self.pool = None
        self._create_pool(min_connections, max_connections)
    
    def _create_pool(self, min_connections: int, max_connections: int):
        """Create connection pool."""
        try:
            self.pool = SimpleConnectionPool(
                minconn=min_connections,
                maxconn=max_connections,
                dsn=self.database_url
            )
            logger.info(f"Database connection pool created ({min_connections}-{max_connections} connections)")
        except Exception as e:
            logger.error(f"Failed to create database connection pool: {e}")
            raise
    
    @contextmanager
    def get_connection(self):
        """Context manager to get database connection from pool."""
        connection = None
        try:
            connection = self.pool.getconn()
            yield connection
        except Exception as e:
            if connection:
                connection.rollback()
            logger.error(f"Database operation failed: {e}")
            raise
        finally:
            if connection:
                self.pool.putconn(connection)
    
    def create_task(self, task_data: TaskCreate) -> Task:
        """
        Create a new task in the database with transactional support.

        Args:
            task_data: Task creation data

        Returns:
            Created task with generated ID, timestamp, and assignee list
        """
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Begin transaction (auto-started by psycopg2)
                try:
                    # Insert into tasks table
                    insert_task_query = """
                    INSERT INTO tasks (created_by, title, description, broadcast_updates)
                    VALUES (%(created_by)s, %(title)s, %(description)s, %(broadcast_updates)s)
                    RETURNING id, created_at, created_by, title, description, status, broadcast_updates
                    """

                    cur.execute(insert_task_query, {
                        'created_by': task_data.created_by,
                        'title': task_data.title,
                        'description': task_data.description,
                        'broadcast_updates': task_data.broadcast_updates if task_data.broadcast_updates is not None else False
                    })

                    task_row = cur.fetchone()
                    task_id = task_row['id']

                    # Insert assignees if provided
                    assignee_ids = []
                    if task_data.assignee_ids:
                        insert_assignee_query = """
                        INSERT INTO task_assignees (task_id, agent_id)
                        VALUES (%s, %s)
                        """

                        for assignee_id in task_data.assignee_ids:
                            cur.execute(insert_assignee_query, (task_id, assignee_id))
                            assignee_ids.append(assignee_id)

                    # Commit transaction
                    conn.commit()

                    # Return the complete task
                    return Task(
                        id=task_row['id'],
                        created_at=task_row['created_at'],
                        created_by=task_row['created_by'],
                        title=task_row['title'],
                        description=task_row['description'],
                        status=task_row['status'],
                        assignee_ids=assignee_ids,
                        broadcast_updates=task_row['broadcast_updates']
                    )

                except Exception as e:
                    conn.rollback()
                    logger.error(f"Failed to create task: {e}")
                    raise
    
    def get_task(self, task_id: int) -> Optional[Task]:
        """
        Get a task by its ID with JOIN to collect assignee_ids.

        Args:
            task_id: Task ID

        Returns:
            Task if found, None otherwise
        """
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Query task with assignees using LEFT JOIN to include tasks with no assignees
                query = """
                SELECT
                    t.id, t.created_at, t.created_by, t.title, t.description, t.status, t.broadcast_updates,
                    COALESCE(
                        ARRAY_AGG(ta.agent_id) FILTER (WHERE ta.agent_id IS NOT NULL),
                        ARRAY[]::VARCHAR[]
                    ) as assignee_ids
                FROM tasks t
                LEFT JOIN task_assignees ta ON t.id = ta.task_id
                WHERE t.id = %s
                GROUP BY t.id, t.created_at, t.created_by, t.title, t.description, t.status, t.broadcast_updates
                """

                cur.execute(query, (task_id,))
                row = cur.fetchone()

                if not row:
                    return None

                return Task(
                    id=row['id'],
                    created_at=row['created_at'],
                    created_by=row['created_by'],
                    title=row['title'],
                    description=row['description'],
                    status=row['status'],
                    assignee_ids=list(row['assignee_ids']) if row['assignee_ids'] else [],
                    broadcast_updates=row['broadcast_updates']
                )
    
    def update_task(self, task_id: int, task_data: TaskUpdate) -> Optional[Task]:
        """
        Update a task with partial updates and assignee replacement.

        Args:
            task_id: Task ID
            task_data: Task update data (all fields optional)

        Returns:
            Updated task if found, None otherwise
        """
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                try:
                    # Build dynamic update query for tasks table
                    update_fields = []
                    update_values = {}

                    if task_data.title is not None:
                        update_fields.append("title = %(title)s")
                        update_values['title'] = task_data.title

                    if task_data.description is not None:
                        update_fields.append("description = %(description)s")
                        update_values['description'] = task_data.description

                    if task_data.status is not None:
                        update_fields.append("status = %(status)s")
                        update_values['status'] = task_data.status

                    if task_data.broadcast_updates is not None:
                        update_fields.append("broadcast_updates = %(broadcast_updates)s")
                        update_values['broadcast_updates'] = task_data.broadcast_updates

                    # Update tasks table if there are fields to update
                    if update_fields:
                        update_query = f"""
                        UPDATE tasks
                        SET {', '.join(update_fields)}
                        WHERE id = %(task_id)s
                        RETURNING id
                        """
                        update_values['task_id'] = task_id

                        cur.execute(update_query, update_values)
                        if not cur.fetchone():
                            # Task not found
                            return None
                    else:
                        # Check if task exists
                        cur.execute("SELECT id FROM tasks WHERE id = %s", (task_id,))
                        if not cur.fetchone():
                            return None

                    # Handle assignee updates if provided
                    if task_data.assignee_ids is not None:
                        # Delete existing assignees
                        cur.execute("DELETE FROM task_assignees WHERE task_id = %s", (task_id,))

                        # Insert new assignees
                        if task_data.assignee_ids:
                            insert_assignee_query = """
                            INSERT INTO task_assignees (task_id, agent_id)
                            VALUES (%s, %s)
                            """
                            for assignee_id in task_data.assignee_ids:
                                cur.execute(insert_assignee_query, (task_id, assignee_id))

                    # Commit transaction
                    conn.commit()

                    # Return the updated task
                    return self.get_task(task_id)

                except Exception as e:
                    conn.rollback()
                    logger.error(f"Failed to update task {task_id}: {e}")
                    raise
    
    def delete_task(self, task_id: int) -> bool:
        """
        Delete a task. CASCADE will automatically remove related task_assignees.

        Args:
            task_id: Task ID

        Returns:
            True if a row was deleted, False otherwise
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                query = "DELETE FROM tasks WHERE id = %s"
                cur.execute(query, (task_id,))
                conn.commit()
                return cur.rowcount > 0
    
    def get_tasks(
        self,
        status: Optional[List[str]] = None,
        creator_id: Optional[str] = None,
        assignee_id: Optional[str] = None
    ) -> List[Task]:
        """
        Get tasks with dynamic filtering and default status logic.

        Args:
            status: List of statuses to filter by. Defaults to ['pending', 'in_progress', 'completed']
            creator_id: Filter by task creator
            assignee_id: Filter by assignee

        Returns:
            List of tasks matching the filters
        """
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Default status filter excludes 'archived'
                if not status:
                    status = ['pending', 'in_progress', 'completed']

                # Build dynamic query
                base_query = """
                SELECT
                    t.id, t.created_at, t.created_by, t.title, t.description, t.status, t.broadcast_updates,
                    COALESCE(
                        ARRAY_AGG(ta.agent_id) FILTER (WHERE ta.agent_id IS NOT NULL),
                        ARRAY[]::VARCHAR[]
                    ) as assignee_ids
                FROM tasks t
                LEFT JOIN task_assignees ta ON t.id = ta.task_id
                """

                # Build WHERE conditions
                where_conditions = []
                query_params = []

                # Status filter
                if status:
                    placeholders = ','.join(['%s'] * len(status))
                    where_conditions.append(f"t.status IN ({placeholders})")
                    query_params.extend(status)

                # Creator filter
                if creator_id:
                    where_conditions.append("t.created_by = %s")
                    query_params.append(creator_id)

                # Assignee filter - need special handling since it's in the join table
                if assignee_id:
                    where_conditions.append("t.id IN (SELECT task_id FROM task_assignees WHERE agent_id = %s)")
                    query_params.append(assignee_id)

                # Combine query parts
                if where_conditions:
                    query = f"{base_query} WHERE {' AND '.join(where_conditions)}"
                else:
                    query = base_query

                query += " GROUP BY t.id, t.created_at, t.created_by, t.title, t.description, t.status, t.broadcast_updates"
                query += " ORDER BY t.created_at DESC"

                cur.execute(query, query_params)
                rows = cur.fetchall()

                return [
                    Task(
                        id=row['id'],
                        created_at=row['created_at'],
                        created_by=row['created_by'],
                        title=row['title'],
                        description=row['description'],
                        status=row['status'],
                        assignee_ids=list(row['assignee_ids']) if row['assignee_ids'] else [],
                        broadcast_updates=row['broadcast_updates']
                    )
                    for row in rows
                ]
    
    def execute_query(self, query: str, params: tuple = None):
        """
        Execute a raw SQL query and return results.

        Args:
            query: SQL query string
            params: Query parameters tuple

        Returns:
            List of tuples with query results
        """
        logger.info(f"Executing query: {query[:100]}... with params: {params}")
        with self.get_connection() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(query, params)

                    # Check if it's a SELECT query, CTE (WITH), or has RETURNING
                    query_upper = query.strip().upper()
                    if query_upper.startswith('SELECT') or query_upper.startswith('WITH') or 'RETURNING' in query_upper:
                        result = cursor.fetchall()
                        connection.commit()  # Commit even for SELECT with RETURNING
                        logger.info(f"Query executed successfully, returning {len(result) if result else 0} rows")
                        return result
                    else:
                        # For INSERT, UPDATE, DELETE operations
                        connection.commit()
                        rowcount = cursor.rowcount
                        logger.info(f"Query executed successfully, affected {rowcount} rows")
                        return rowcount
            except Exception as e:
                connection.rollback()
                logger.error(f"Error executing query: {e}")
                raise

    def execute_query_dict(self, query: str, params: tuple = None):
        """
        Execute a raw SQL query and return results as dictionaries.

        Args:
            query: SQL query string
            params: Query parameters tuple

        Returns:
            List of dictionaries with query results
        """
        logger.info(f"Executing query: {query[:100]}... with params: {params}")
        with self.get_connection() as connection:
            try:
                with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute(query, params)

                    # Check if it's a SELECT query, CTE (WITH), or has RETURNING
                    query_upper = query.strip().upper()
                    if query_upper.startswith('SELECT') or query_upper.startswith('WITH') or 'RETURNING' in query_upper:
                        result = cursor.fetchall()
                        connection.commit()  # Commit even for SELECT with RETURNING
                        logger.info(f"Query executed successfully, returning {len(result) if result else 0} rows")
                        return result
                    else:
                        # For INSERT, UPDATE, DELETE operations
                        connection.commit()
                        rowcount = cursor.rowcount
                        logger.info(f"Query executed successfully, affected {rowcount} rows")
                        return rowcount
            except Exception as e:
                logger.error(f"Query execution failed: {e}")
                connection.rollback()
                raise
    
    def close(self):
        """Close all connections in the pool."""
        if self.pool:
            self.pool.closeall()
            logger.info("Database connection pool closed")


# Global database client instance
db_client = None


def get_database() -> DatabaseClient:
    """Get the global database client instance."""
    # Import here to avoid circular imports
    from app.main import db_client
    
    if not db_client:
        raise RuntimeError("Database client not initialized. Ensure FastAPI app has started.")
    return db_client


def close_database():
    """Close the global database client."""
    global db_client
    if db_client:
        db_client.close()
        db_client = None