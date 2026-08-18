"""
Custom Gemini Embedding integration for LlamaIndex
Uses Google's Generative AI embeddings (embedding-001 model)
"""
import logging
import os
from typing import Any, List

from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.bridge.pydantic import PrivateAttr

try:
    import google.genai as genai
    from google.genai.types import EmbedContentConfig
    USE_NEW_SDK = True
except ImportError:
    import google.generativeai as genai
    USE_NEW_SDK = False

logger = logging.getLogger(__name__)


class GeminiEmbedding(BaseEmbedding):
    """Gemini embedding model for LlamaIndex"""

    _model: Any = PrivateAttr()
    _model_name: str = PrivateAttr()

    def __init__(
        self,
        model_name: str = "models/gemini-embedding-001",
        api_key: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize Gemini embedding model
        
        Args:
            model_name: Name of the Gemini embedding model
            api_key: Google API key (defaults to GEMINI_API_KEY env var)
            **kwargs: Additional arguments passed to BaseEmbedding
        """
        super().__init__(**kwargs)

        self._model_name = model_name

        # Configure Gemini API
        api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable is required")

        if USE_NEW_SDK:
            # Use new google.genai SDK
            self._client = genai.Client(api_key=api_key)
            logger.info(f"Initialized Gemini embedding model using new SDK: {model_name}")
        else:
            # Use legacy google.generativeai SDK
            genai.configure(api_key=api_key)
            logger.info(f"Initialized Gemini embedding model using legacy SDK: {model_name}")

    @classmethod
    def class_name(cls) -> str:
        return "GeminiEmbedding"

    async def _aget_query_embedding(self, query: str) -> List[float]:
        """Get embedding for a query asynchronously"""
        return await self._aget_text_embedding(query)

    async def _aget_text_embedding(self, text: str) -> List[float]:
        """Get embedding for text asynchronously"""
        try:
            if USE_NEW_SDK:
                # Use new google.genai SDK - note: this is synchronous in the new SDK
                response = self._client.models.embed_content(
                    model=self._model_name,
                    contents=text,
                    config=EmbedContentConfig(task_type="RETRIEVAL_QUERY")
                )
                return response.embeddings[0].values
            else:
                # Use legacy google.generativeai SDK
                result = genai.embed_content(
                    model=self._model_name,
                    content=text,
                    task_type="retrieval_query",
                )
                return result['embedding']
        except Exception as e:
            logger.error(f"Error generating embedding: {e}")
            raise

    def _get_query_embedding(self, query: str) -> List[float]:
        """Get embedding for a query synchronously"""
        return self._get_text_embedding(query)

    def _get_text_embedding(self, text: str) -> List[float]:
        """Get embedding for text synchronously"""
        try:
            if USE_NEW_SDK:
                # Use new google.genai SDK
                response = self._client.models.embed_content(
                    model=self._model_name,
                    contents=text,
                    config=EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT")
                )
                return response.embeddings[0].values
            else:
                # Use legacy google.generativeai SDK
                result = genai.embed_content(
                    model=self._model_name,
                    content=text,
                    task_type="retrieval_document",
                )
                return result['embedding']
        except Exception as e:
            logger.error(f"Error generating embedding: {e}")
            raise

    def _get_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Get embeddings for multiple texts synchronously"""
        return [self._get_text_embedding(text) for text in texts]

    async def _aget_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Get embeddings for multiple texts asynchronously"""
        # Note: Gemini doesn't have batch embedding endpoint yet
        # So we process them sequentially
        embeddings = []
        for text in texts:
            embedding = await self._aget_text_embedding(text)
            embeddings.append(embedding)
        return embeddings
