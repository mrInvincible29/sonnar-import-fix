"""
Utility decorators for retry logic and rate limiting.
"""

import time
import logging
from functools import wraps
from typing import Any, Callable, Optional, Type, Union
from collections import defaultdict


logger = logging.getLogger(__name__)


class RetryError(Exception):
    """Raised when all retry attempts are exhausted."""
    pass


def retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: Union[Type[Exception], tuple] = Exception,
    logger_name: Optional[str] = None
):
    """
    Decorator that retries a function call on failure.
    
    Args:
        max_attempts: Maximum number of attempts
        delay: Initial delay between retries in seconds
        backoff: Multiplier for delay between retries
        exceptions: Exception types to catch and retry on
        logger_name: Name of logger to use for retry messages
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            retry_logger = logging.getLogger(logger_name or __name__)
            
            last_exception = None
            current_delay = delay
            
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    
                    if attempt == max_attempts - 1:
                        # Last attempt failed
                        retry_logger.error(
                            f"Function {func.__name__} failed after {max_attempts} attempts. "
                            f"Last error: {e}"
                        )
                        raise RetryError(f"Failed after {max_attempts} attempts") from e
                    
                    retry_logger.warning(
                        f"Function {func.__name__} failed on attempt {attempt + 1}/{max_attempts}. "
                        f"Retrying in {current_delay:.1f}s. Error: {e}"
                    )
                    
                    time.sleep(current_delay)
                    current_delay *= backoff
            
            # This should never be reached, but just in case
            raise last_exception
        
        return wrapper
    return decorator


class RateLimiter:
    """Rate limiter to control request frequency."""
    
    def __init__(self):
        self.request_counts = defaultdict(list)
    
    def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> bool:
        """
        Check if request is allowed under rate limit.
        
        Args:
            key: Identifier for rate limiting (e.g., IP address)
            max_requests: Maximum requests allowed
            window_seconds: Time window in seconds
        
        Returns:
            True if request is allowed, False otherwise
        """
        current_time = time.time()
        
        # Clean old entries
        self.request_counts[key] = [
            req_time for req_time in self.request_counts[key]
            if current_time - req_time < window_seconds
        ]
        
        # Check rate limit
        if len(self.request_counts[key]) >= max_requests:
            return False
        
        # Record request
        self.request_counts[key].append(current_time)
        return True
    
    def get_request_count(self, key: str, window_seconds: int) -> int:
        """Get current request count for a key within the time window."""
        current_time = time.time()
        
        # Clean old entries
        self.request_counts[key] = [
            req_time for req_time in self.request_counts[key]
            if current_time - req_time < window_seconds
        ]
        
        return len(self.request_counts[key])


# Global rate limiter instance
_rate_limiter = RateLimiter()


def rate_limit(max_requests: int = 30, window_seconds: int = 60, key_func: Optional[Callable] = None):
    """
    Decorator that implements rate limiting.
    
    Args:
        max_requests: Maximum requests allowed
        window_seconds: Time window in seconds
        key_func: Function to extract rate limiting key from args/kwargs
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            # Determine rate limiting key
            if key_func:
                key = key_func(*args, **kwargs)
            else:
                # Default to function name
                key = f"{func.__module__}.{func.__name__}"
            
            if not _rate_limiter.is_allowed(key, max_requests, window_seconds):
                current_count = _rate_limiter.get_request_count(key, window_seconds)
                raise Exception(
                    f"Rate limit exceeded for {func.__name__}. "
                    f"{current_count}/{max_requests} requests in {window_seconds}s window."
                )
            
            return func(*args, **kwargs)
        
        return wrapper
    return decorator


def log_execution_time(logger_name: Optional[str] = None):
    """
    Decorator that logs function execution time.
    
    Args:
        logger_name: Name of logger to use
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            exec_logger = logging.getLogger(logger_name or __name__)
            
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                execution_time = time.time() - start_time
                exec_logger.debug(f"{func.__name__} completed in {execution_time:.2f}s")
                return result
            except Exception as e:
                execution_time = time.time() - start_time
                exec_logger.error(f"{func.__name__} failed after {execution_time:.2f}s: {e}")
                raise
        
        return wrapper
    return decorator