import logging
import os
from typing import Optional

import google.generativeai as genai
from google.generativeai.types import GenerateContentResponse
import google.api_core.exceptions

logger = logging.getLogger(__name__)


class GeminiClient:
    """Client for interacting with Google's Gemini API."""
    
    def __init__(self, model_name: str = "gemini-3-flash-preview"):
        """
        Initialize the Gemini client with API credentials.
        
        Args:
            model_name: The name of the Gemini model to use (default: gemini-pro)
        """
        # Load API key from environment
        self.api_key = os.environ.get("GEMINI_API_KEY")
        
        if not self.api_key:
            logger.error("GEMINI_API_KEY not found in environment variables")
            raise ValueError("GEMINI_API_KEY environment variable is required")
        
        # Configure the genai library with the API key
        genai.configure(api_key=self.api_key)
        
        # Initialize the model
        try:
            self.model = genai.GenerativeModel(model_name)
            self.model_name = model_name
            logger.info(f"Gemini client initialized with model: {model_name}")
        except Exception as e:
            logger.error(f"Failed to initialize Gemini model: {e}")
            raise
    
    def get_response(self, prompt: str) -> str:
        """
        Get a response from the Gemini model for the given prompt.
        
        Args:
            prompt: The input prompt to send to the model
            
        Returns:
            The model's response text as a string, or an error message if the API call fails
        """
        if not prompt:
            logger.warning("Empty prompt provided")
            return "Error: Empty prompt provided"
        
        try:
            # Generate content using the model
            logger.debug(f"Sending prompt to {self.model_name}: {prompt[:100]}...")
            response = self.model.generate_content(prompt)
            
            # Extract and return the response text
            if response and response.text:
                logger.info(f"Successfully received response from {self.model_name}")
                return response.text
            else:
                logger.warning("Received empty response from model")
                return "Error: Received empty response from model"
                
        except google.api_core.exceptions.ResourceExhausted as e:
            error_msg = f"API quota/rate limit exceeded: {e}"
            logger.error(error_msg)
            return f"Error: {error_msg}"
            
        except google.api_core.exceptions.InvalidArgument as e:
            error_msg = f"Invalid argument error: {e}"
            logger.error(error_msg)
            return f"Error: {error_msg}"
            
        except google.api_core.exceptions.PermissionDenied as e:
            error_msg = f"Permission denied (check API key): {e}"
            logger.error(error_msg)
            return f"Error: {error_msg}"
            
        except google.api_core.exceptions.NotFound as e:
            error_msg = f"Model or resource not found: {e}"
            logger.error(error_msg)
            return f"Error: {error_msg}"
            
        except google.api_core.exceptions.GoogleAPIError as e:
            error_msg = f"Google API error: {e}"
            logger.error(error_msg)
            return f"Error: {error_msg}"
            
        except Exception as e:
            error_msg = f"Unexpected error calling Gemini API: {e}"
            logger.error(error_msg)
            return f"Error: {error_msg}"
    
    def get_response_with_context(self, prompt: str, context: Optional[str] = None) -> str:
        """
        Get a response with optional context prepended to the prompt.
        
        Args:
            prompt: The main prompt to send to the model
            context: Optional context to prepend to the prompt
            
        Returns:
            The model's response text as a string
        """
        if context:
            full_prompt = f"{context}\n\n{prompt}"
        else:
            full_prompt = prompt
            
        return self.get_response(full_prompt)
    
    def __repr__(self) -> str:
        """String representation of the GeminiClient."""
        return f"GeminiClient(model='{self.model_name}')"