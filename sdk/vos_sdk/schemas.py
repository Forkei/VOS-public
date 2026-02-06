from pydantic import BaseModel
from typing import Optional, Dict, Any

class ToolResult(BaseModel):
    """
    Standardized result model for all VOS tool operations.
    
    This ensures consistent communication between agents and tools across the VOS ecosystem.
    All tools must return this format to maintain interoperability.
    """
    tool_name: str  # Name of the tool that was executed
    status: str  # 'SUCCESS' or 'FAILURE'
    result: Optional[Dict[str, Any]] = None  # Successful operation result data
    error_message: Optional[str] = None  # Error description if status is 'FAILURE'
    
    class Config:
        json_encoders = {
            # Add any custom encoders if needed
        }
    
    @classmethod
    def success(cls, tool_name: str, result: Dict[str, Any]) -> "ToolResult":
        """
        Create a successful ToolResult.
        
        Args:
            tool_name: Name of the tool
            result: Result data from the successful operation
            
        Returns:
            ToolResult with SUCCESS status
        """
        return cls(
            tool_name=tool_name,
            status="SUCCESS",
            result=result,
            error_message=None
        )
    
    @classmethod
    def failure(cls, tool_name: str, error_message: str) -> "ToolResult":
        """
        Create a failed ToolResult.
        
        Args:
            tool_name: Name of the tool
            error_message: Description of the error
            
        Returns:
            ToolResult with FAILURE status
        """
        return cls(
            tool_name=tool_name,
            status="FAILURE",
            result=None,
            error_message=error_message
        )