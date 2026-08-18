"""
Custom Gemini LLM integration for LlamaIndex
Uses Google's Generative AI (Gemini) for text generation
"""
import logging
import os
from collections.abc import Sequence
from typing import Any, Optional

from llama_index.core.base.llms.types import (
    ChatMessage,
    ChatResponse,
    ChatResponseGen,
    CompletionResponse,
    CompletionResponseGen,
    LLMMetadata,
    MessageRole,
)
from llama_index.core.bridge.pydantic import Field, PrivateAttr
from llama_index.core.llms.callbacks import llm_chat_callback, llm_completion_callback
from llama_index.core.llms.custom import CustomLLM

try:
    import google.generativeai as genai
    from google.generativeai.types import GenerationConfig
    USE_NEW_SDK = False
except ImportError:
    import google.genai as genai
    from google.genai.types import Content, GenerateContentConfig, Part
    USE_NEW_SDK = True

logger = logging.getLogger(__name__)


class GeminiLLM(CustomLLM):
    """Gemini LLM for LlamaIndex"""

    model_name: str = Field(default="gemini-1.5-flash")
    """The Gemini model to use."""

    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    """Sampling temperature."""

    max_tokens: int = Field(default=2048, gt=0)
    """Maximum number of tokens to generate."""

    api_key: Optional[str] = Field(default=None)
    """Google API key. If not provided, will look for GEMINI_API_KEY env var."""

    _client: Any = PrivateAttr()
    _model: Any = PrivateAttr()

    def __init__(
        self,
        model_name: str = "gemini-1.5-flash",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        api_key: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """Initialize Gemini LLM
        
        Args:
            model_name: Name of the Gemini model to use
            temperature: Sampling temperature (0.0 to 2.0)
            max_tokens: Maximum tokens to generate
            api_key: Google API key (defaults to GEMINI_API_KEY env var)
            **kwargs: Additional arguments passed to CustomLLM
        """
        super().__init__(**kwargs)

        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens

        # Configure Gemini API
        api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable is required")

        if USE_NEW_SDK:
            # Use new google.genai SDK
            self._client = genai.Client(api_key=api_key)
            logger.info(f"Initialized Gemini LLM using new SDK: {model_name}")
        else:
            # Use legacy google.generativeai SDK
            genai.configure(api_key=api_key)
            self._model = genai.GenerativeModel(model_name)
            logger.info(f"Initialized Gemini LLM using legacy SDK: {model_name}")

    @classmethod
    def class_name(cls) -> str:
        return "GeminiLLM"

    @property
    def metadata(self) -> LLMMetadata:
        """Get LLM metadata."""
        return LLMMetadata(
            model_name=self.model_name,
            context_window=30720,  # Gemini 1.5 Flash context window
            num_output=self.max_tokens,
            is_chat_model=True,
            is_function_calling_model=False,
            model_kwargs={
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            },
        )

    @llm_chat_callback()
    def chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponse:
        """Chat with the model."""
        try:
            # Convert messages to Gemini format
            if USE_NEW_SDK:
                # Use new google.genai SDK
                gemini_messages = []
                for message in messages:
                    role = "user" if message.role == MessageRole.USER else "model"
                    content = Content(parts=[Part(text=message.content)], role=role)
                    gemini_messages.append(content)

                config = GenerateContentConfig(
                    temperature=self.temperature,
                    max_output_tokens=self.max_tokens,
                )

                response = self._client.models.generate_content(
                    model=self.model_name,
                    contents=gemini_messages,
                    config=config
                )

                return ChatResponse(
                    message=ChatMessage(
                        role=MessageRole.ASSISTANT,
                        content=response.text,
                    ),
                    raw=response,
                )
            else:
                # Use legacy google.generativeai SDK
                # Convert messages to Gemini format
                gemini_messages = []
                for message in messages:
                    role = "user" if message.role == MessageRole.USER else "model"
                    gemini_messages.append({"role": role, "parts": [message.content]})

                generation_config = GenerationConfig(
                    temperature=self.temperature,
                    max_output_tokens=self.max_tokens,
                )

                chat = self._model.start_chat(history=gemini_messages[:-1])
                response = chat.send_message(
                    gemini_messages[-1]["parts"][0],
                    generation_config=generation_config
                )

                return ChatResponse(
                    message=ChatMessage(
                        role=MessageRole.ASSISTANT,
                        content=response.text,
                    ),
                    raw=response,
                )

        except Exception as e:
            logger.error(f"Error in Gemini chat: {e}")
            raise

    @llm_completion_callback()
    def complete(self, prompt: str, **kwargs: Any) -> CompletionResponse:
        """Complete a prompt."""
        try:
            # Convert to chat format for consistency
            messages = [
                ChatMessage(role=MessageRole.USER, content=prompt)
            ]
            chat_response = self.chat(messages, **kwargs)

            return CompletionResponse(
                text=chat_response.message.content,
                raw=chat_response.raw,
            )
        except Exception as e:
            logger.error(f"Error in Gemini completion: {e}")
            raise

    @llm_chat_callback()
    def stream_chat(
        self, messages: Sequence[ChatMessage], **kwargs: Any
    ) -> ChatResponseGen:
        """Stream chat with the model."""
        # For now, just return the non-streaming version
        response = self.chat(messages, **kwargs)
        yield response

    @llm_completion_callback()
    def stream_complete(
        self, prompt: str, **kwargs: Any
    ) -> CompletionResponseGen:
        """Stream complete a prompt."""
        # For now, just return the non-streaming version
        response = self.complete(prompt, **kwargs)
        yield response
