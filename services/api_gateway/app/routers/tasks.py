from typing import List, Optional
import os
import logging

from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import JSONResponse

from app.schemas import Task, TaskCreate, TaskUpdate
from app.database import get_database, DatabaseClient
from app.task_notifications import (
    send_task_created_notification,
    send_task_status_change_notification,
    send_task_assignment_notification,
    send_task_unassignment_notification
)

logger = logging.getLogger(__name__)

# Create router for task endpoints
router = APIRouter(prefix="/tasks", tags=["tasks"])


def get_db() -> DatabaseClient:
    """Dependency to get database client instance."""
    return get_database()


@router.post("/", response_model=Task, status_code=201)
async def create_task(
    task_data: TaskCreate,
    db: DatabaseClient = Depends(get_db)
) -> Task:
    """
    Create a new task.

    Args:
        task_data: Task creation data
        db: Database client dependency

    Returns:
        Created task with generated ID and timestamp
    """
    try:
        # Create the task
        task = db.create_task(task_data)

        # Send notifications if broadcast_updates is enabled and there are assignees
        if task.broadcast_updates and task.assignee_ids:
            rabbitmq_url = os.getenv("RABBITMQ_URL", "amqp://vos_user:password@rabbitmq:5672/vos_vhost")
            try:
                send_task_created_notification(
                    rabbitmq_url=rabbitmq_url,
                    assignee_ids=task.assignee_ids,
                    task_id=task.id,
                    task_title=task.title,
                    task_status=task.status,
                    created_by=task.created_by
                )
                logger.info(f"✅ Sent task creation notifications for task {task.id} to {len(task.assignee_ids)} agents")
            except Exception as e:
                # Don't fail the request if notification fails, just log it
                logger.error(f"⚠️ Failed to send task creation notifications: {e}")

        return task
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create task: {str(e)}"
        )


@router.get("/{task_id}", response_model=Task)
async def get_task(
    task_id: int,
    db: DatabaseClient = Depends(get_db)
) -> Task:
    """
    Get a single task by its ID.

    Args:
        task_id: Task ID
        db: Database client dependency

    Returns:
        Task if found

    Raises:
        404: If task not found
    """
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=404,
            detail=f"Task with ID {task_id} not found"
        )
    return task


@router.get("/", response_model=List[Task])
async def get_tasks(
    status: Optional[List[str]] = Query(
        default=None,
        description="Filter by task status. Defaults to ['pending', 'in_progress', 'completed'] (excludes 'archived')"
    ),
    creator_id: Optional[str] = Query(
        default=None,
        description="Filter by task creator ID"
    ),
    assignee_id: Optional[str] = Query(
        default=None,
        description="Filter by assignee ID"
    ),
    db: DatabaseClient = Depends(get_db)
) -> List[Task]:
    """
    Get tasks with optional filtering.
    
    Args:
        status: List of statuses to filter by (defaults to non-archived tasks)
        creator_id: Filter by task creator
        assignee_id: Filter by assignee
        db: Database client dependency
        
    Returns:
        List of tasks matching the filters
    """
    try:
        return db.get_tasks(
            status=status,
            creator_id=creator_id,
            assignee_id=assignee_id
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve tasks: {str(e)}"
        )


@router.patch("/{task_id}", response_model=Task)
async def update_task(
    task_id: int,
    task_data: TaskUpdate,
    db: DatabaseClient = Depends(get_db)
) -> Task:
    """
    Update an existing task (partial updates).

    Args:
        task_id: Task ID
        task_data: Task update data (all fields optional)
        db: Database client dependency

    Returns:
        Updated task

    Raises:
        404: If task not found
    """
    try:
        # Get the old task to compare changes
        old_task = db.get_task(task_id)
        if not old_task:
            raise HTTPException(
                status_code=404,
                detail=f"Task with ID {task_id} not found"
            )

        # Update the task
        updated_task = db.update_task(task_id, task_data)
        if not updated_task:
            raise HTTPException(
                status_code=404,
                detail=f"Task with ID {task_id} not found"
            )

        # Send notifications if broadcast_updates is enabled
        if updated_task.broadcast_updates:
            rabbitmq_url = os.getenv("RABBITMQ_URL", "amqp://vos_user:password@rabbitmq:5672/vos_vhost")

            try:
                # Notify on status change
                if task_data.status and task_data.status != old_task.status and updated_task.assignee_ids:
                    send_task_status_change_notification(
                        rabbitmq_url=rabbitmq_url,
                        assignee_ids=updated_task.assignee_ids,
                        task_id=updated_task.id,
                        task_title=updated_task.title,
                        old_status=old_task.status,
                        new_status=updated_task.status
                    )
                    logger.info(f"✅ Sent status change notifications for task {task_id}")

                # Notify on assignee changes
                if task_data.assignee_ids is not None:
                    old_assignees = set(old_task.assignee_ids)
                    new_assignees = set(updated_task.assignee_ids)

                    # Notify newly assigned agents
                    added_assignees = new_assignees - old_assignees
                    for agent_id in added_assignees:
                        send_task_assignment_notification(
                            rabbitmq_url=rabbitmq_url,
                            agent_id=agent_id,
                            task_id=updated_task.id,
                            task_title=updated_task.title,
                            task_status=updated_task.status
                        )
                    if added_assignees:
                        logger.info(f"✅ Sent assignment notifications to {len(added_assignees)} agents for task {task_id}")

                    # Notify unassigned agents
                    removed_assignees = old_assignees - new_assignees
                    for agent_id in removed_assignees:
                        send_task_unassignment_notification(
                            rabbitmq_url=rabbitmq_url,
                            agent_id=agent_id,
                            task_id=updated_task.id,
                            task_title=updated_task.title,
                            task_status=updated_task.status
                        )
                    if removed_assignees:
                        logger.info(f"✅ Sent unassignment notifications to {len(removed_assignees)} agents for task {task_id}")

            except Exception as e:
                # Don't fail the request if notification fails, just log it
                logger.error(f"⚠️ Failed to send task update notifications: {e}")

        return updated_task
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update task: {str(e)}"
        )


@router.delete("/{task_id}")
async def delete_task(
    task_id: int,
    db: DatabaseClient = Depends(get_db)
) -> JSONResponse:
    """
    Delete a task.

    Args:
        task_id: Task ID
        db: Database client dependency

    Returns:
        Success message

    Raises:
        404: If task not found
    """
    try:
        success = db.delete_task(task_id)
        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"Task with ID {task_id} not found"
            )
        return JSONResponse(
            status_code=200,
            content={"message": f"Task {task_id} deleted successfully"}
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete task: {str(e)}"
        )