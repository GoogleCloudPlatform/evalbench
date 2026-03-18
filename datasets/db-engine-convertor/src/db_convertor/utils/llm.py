"""LLM utilities for database conversion."""

import logging
import functools
import time
import os


def retry_on_quota_exceeded(max_attempts=30, initial_delay=1.0, backoff_factor=2):
    """A decorator for retrying a function call with exponential backoff on quota errors.

    Retries if the error message contains "Quota exceeded" or "Content has no parts".

    Args:
        max_attempts: Maximum number of attempts.
        initial_delay: Initial delay between retries in seconds.
        backoff_factor: Factor by which the delay increases after each retry.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            attempts = 0
            delay = initial_delay
            while attempts < max_attempts:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if (
                        "429" in str(e)
                        or "529" in str(e)
                        or "503" in str(e)
                        or "Quota exceeded" in str(e)
                        or "Content has no parts" in str(e)
                        or "Cannot get the response text" in str(e)
                    ) and attempts < max_attempts:
                        logging.warning(
                            f"Retriable error: {e}, retrying {attempts + 1}/{max_attempts} in {delay:.1f}s"
                        )
                        time.sleep(delay)
                        delay *= backoff_factor
                        attempts += 1
                    else:
                        logging.error(f"Final attempt failed or non-quota error: {e}")
                        raise
            return None
        return wrapper
    return decorator


@retry_on_quota_exceeded()
def gemini_inference_3_flash(prompt, temperature: float = 0.3, enforce_json=True):
    """Call Gemini 3 Flash API for inference.
    
    Args:
        prompt: The prompt to send to Gemini
        temperature: Sampling temperature
        enforce_json: Whether to enforce JSON output
        
    Returns:
        The response text from Gemini
    """
    from google import genai
    from google.genai import types
    
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or ""
    VERTEX_GCP_PROJECT = os.environ.get("GCP_PROJECT_ID") or ""
    api_key = GEMINI_API_KEY
    
    if api_key:
        client = genai.Client(api_key=api_key)
    else:
        client = genai.Client(
            vertexai=True,
            project=VERTEX_GCP_PROJECT,
            location="global",
        )
    
    model = "gemini-3-flash-preview"
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=prompt),
            ],
        ),
    ]
    
    if enforce_json:
        mime_type = "application/json"
    else:
        mime_type = "text/plain"
        
    generate_content_config = types.GenerateContentConfig(
        response_mime_type=mime_type,
        temperature=temperature,
        thinking_config=types.ThinkingConfig(
            thinking_level=types.ThinkingLevel.HIGH,
        ),
        safety_settings=[
            types.SafetySetting(
                category="HARM_CATEGORY_HATE_SPEECH",
                threshold="OFF"
            ),
            types.SafetySetting(
                category="HARM_CATEGORY_DANGEROUS_CONTENT",
                threshold="OFF"
            ),
            types.SafetySetting(
                category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                threshold="OFF"
            ),
            types.SafetySetting(
                category="HARM_CATEGORY_HARASSMENT",
                threshold="OFF"
            )
        ],
    )
    
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=generate_content_config,
    )
    
    return response.text


@retry_on_quota_exceeded()
def gemini_inference_2_5_flash(prompt, temperature: float = 0.3, enforce_json=True):
    """Call Gemini 2.5 Flash API for inference (default, fast and efficient).
    
    Args:
        prompt: The prompt to send to Gemini
        temperature: Sampling temperature
        enforce_json: Whether to enforce JSON output
        
    Returns:
        The response text from Gemini
    """
    from google import genai
    from google.genai import types
    
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or ""
    VERTEX_GCP_PROJECT = os.environ.get("GCP_PROJECT_ID") or ""
    api_key = GEMINI_API_KEY
    
    if api_key:
        client = genai.Client(api_key=api_key)
    else:
        client = genai.Client(
            vertexai=True,
            project=VERTEX_GCP_PROJECT,
            location="global",
        )
    
    model = "gemini-2.5-flash"
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=prompt),
            ],
        ),
    ]
    
    if enforce_json:
        mime_type = "application/json"
    else:
        mime_type = "text/plain"
        
    generate_content_config = types.GenerateContentConfig(
        response_mime_type=mime_type,
        temperature=temperature,
        safety_settings=[
            types.SafetySetting(
                category="HARM_CATEGORY_HATE_SPEECH",
                threshold="OFF"
            ),
            types.SafetySetting(
                category="HARM_CATEGORY_DANGEROUS_CONTENT",
                threshold="OFF"
            ),
            types.SafetySetting(
                category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                threshold="OFF"
            ),
            types.SafetySetting(
                category="HARM_CATEGORY_HARASSMENT",
                threshold="OFF"
            )
        ],
    )
    
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=generate_content_config,
    )
    
    return response.text


@retry_on_quota_exceeded()
def gemini_inference_2_5_pro(prompt, temperature: float = 0.3, enforce_json=True):
    """Call Gemini 2.5 Pro API for inference (fallback for complex tasks).
    
    Args:
        prompt: The prompt to send to Gemini
        temperature: Sampling temperature
        enforce_json: Whether to enforce JSON output
        
    Returns:
        The response text from Gemini
    """
    from google import genai
    from google.genai import types
    
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or ""
    VERTEX_GCP_PROJECT = os.environ.get("GCP_PROJECT_ID") or ""
    api_key = GEMINI_API_KEY
    
    if api_key:
        client = genai.Client(api_key=api_key)
    else:
        client = genai.Client(
            vertexai=True,
            project=VERTEX_GCP_PROJECT,
            location="global",
        )
    
    model = "gemini-2.5-pro"
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=prompt),
            ],
        ),
    ]
    
    if enforce_json:
        mime_type = "application/json"
    else:
        mime_type = "text/plain"
        
    generate_content_config = types.GenerateContentConfig(
        response_mime_type=mime_type,
        temperature=temperature,
        safety_settings=[
            types.SafetySetting(
                category="HARM_CATEGORY_HATE_SPEECH",
                threshold="OFF"
            ),
            types.SafetySetting(
                category="HARM_CATEGORY_DANGEROUS_CONTENT",
                threshold="OFF"
            ),
            types.SafetySetting(
                category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                threshold="OFF"
            ),
            types.SafetySetting(
                category="HARM_CATEGORY_HARASSMENT",
                threshold="OFF"
            )
        ],
    )
    
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=generate_content_config,
    )
    
    return response.text



@retry_on_quota_exceeded()
def gemini_inference_3_1_pro(prompt, temperature: float = 0.3, enforce_json=True):
    from google import genai
    from google.genai import types
    import os
    
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or ""
    VERTEX_GCP_PROJECT = os.environ.get("GCP_PROJECT_ID") or ""
    api_key = GEMINI_API_KEY
    
    if api_key:
        client = genai.Client(api_key=api_key)
    else:
        client = genai.Client(
            vertexai=True,
            project=VERTEX_GCP_PROJECT,
            location="global",
        )
    
    model = "gemini-3.1-pro-preview"
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=prompt),
            ],
        ),
    ]
    
    config_args = {
        "temperature": temperature,
        "safety_settings": [
            types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF")
        ],
        "thinking_config": types.ThinkingConfig(thinking_level="HIGH")
    }
    
    if enforce_json:
        config_args["response_mime_type"] = "application/json"
        
    generate_content_config = types.GenerateContentConfig(**config_args)
    
    full_text = ""
    for chunk in client.models.generate_content_stream(
        model=model,
        contents=contents,
        config=generate_content_config,
    ):
        if chunk.text:
            full_text += chunk.text
            
    return full_text


@retry_on_quota_exceeded()
def gemini_inference_3_pro(prompt, temperature: float = 0.3, enforce_json=True):
    from google import genai
    from google.genai import types
    import os
    
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or ""
    VERTEX_GCP_PROJECT = os.environ.get("GCP_PROJECT_ID") or ""
    api_key = GEMINI_API_KEY
    
    if api_key:
        client = genai.Client(api_key=api_key)
    else:
        client = genai.Client(
            vertexai=True,
            project=VERTEX_GCP_PROJECT,
            location="global",
        )
    
    model = "gemini-3-pro-preview"
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=prompt),
            ],
        ),
    ]
    
    config_args = {
        "temperature": temperature,
        "safety_settings": [
            types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF")
        ],
        "thinking_config": types.ThinkingConfig(thinking_level="HIGH")
    }
    
    if enforce_json:
        config_args["response_mime_type"] = "application/json"
        
    generate_content_config = types.GenerateContentConfig(**config_args)
    
    full_text = ""
    for chunk in client.models.generate_content_stream(
        model=model,
        contents=contents,
        config=generate_content_config,
    ):
        if chunk.text:
            full_text += chunk.text
            
    return full_text

def gemini_inference(prompt, temperature: float = 0.3, enforce_json=True, use_for_schema=False):
    """Call Gemini API for inference with automatic model selection.
    
    Args:
        prompt: The prompt to send to Gemini
        temperature: Sampling temperature
        enforce_json: Whether to enforce JSON output
        use_for_schema: If True, uses 3.1 Pro -> 3.0 Pro -> 2.5 Pro fallback.
                       If False, uses 2.5 Flash -> 2.5 Pro fallback.
        
    Returns:
        The response text from Gemini
    """
    if use_for_schema:
        # Schema generation: Try Gemini 3.1 Pro, fallback to 3.0 Pro, fallback to 2.5 Pro
        try:
            logging.info("Calling Gemini 3.1 Pro Preview...")
            return gemini_inference_3_1_pro(prompt, temperature, enforce_json)
        except Exception as e:
            logging.warning(f"Gemini 3.1 Pro failed: {e}. Falling back to 3.0 Pro...")
            try:
                logging.info("Calling Gemini 3.0 Pro Preview...")
                return gemini_inference_3_pro(prompt, temperature, enforce_json)
            except Exception as e2:
                logging.warning(f"Gemini 3.0 Pro failed: {e2}. Falling back to 2.5 Pro...")
                return gemini_inference_2_5_pro(prompt, temperature, enforce_json)
    else:
        # Use Gemini 2.5 Flash for query conversion (fast and efficient)
        try:
            return gemini_inference_2_5_flash(prompt, temperature, enforce_json)
        except Exception as e:
            # Fall back to 2.5 Pro if Flash fails
            logging.warning(f"Gemini 2.5 Flash failed: {e}. Falling back to 2.5 Pro...")
            return gemini_inference_2_5_pro(prompt, temperature, enforce_json)
