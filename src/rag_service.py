"""
RAG Service using LlamaIndex with PostgreSQL vector store and Gemini embeddings
"""
import logging
import os
from typing import Optional

from llama_index.core import Settings, StorageContext, VectorStoreIndex
from llama_index.vector_stores.postgres import PGVectorStore
from sqlalchemy import make_url

from gemini_embedding import GeminiEmbedding
from gemini_llm import GeminiLLM

logger = logging.getLogger("rag_service")


class LiveKitRAGService:
    """RAG service using LlamaIndex with PostgreSQL vector store and Gemini embeddings"""

    def __init__(self, org_id: str):
        self.org_id = org_id
        self._index: Optional[VectorStoreIndex] = None
        self._initialized = False
        self._vector_store: Optional[PGVectorStore] = None

    async def initialize(self):
        """Initialize LlamaIndex with PostgreSQL vector store and Gemini embeddings"""
        if self._initialized:
            return

        try:
            Settings.embed_model = GeminiEmbedding(
                model_name="models/gemini-embedding-001"
            )
            Settings.llm = GeminiLLM(
                model_name="gemini-flash-latest",
                temperature=0.7,
                max_tokens=2048
            )
            try:
                probe_vec = await Settings.embed_model._aget_text_embedding("probe")
                embed_dim = len(probe_vec)
                logger.info(f"Detected embedding dimension: {embed_dim}")
            except Exception as e:
                logger.warning(f"Failed to detect embedding dimension, defaulting to 3072: {e}")
                embed_dim = 3072
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                raise ValueError("DATABASE_URL not set")

            connection_string = make_url(database_url)


            self._vector_store = PGVectorStore.from_params(
                database=connection_string.database,
                host=connection_string.host,
                password=connection_string.password,
                port=connection_string.port or 5432,
                user=connection_string.username,
                table_name="kb_chunks",
                embed_dim=embed_dim,
                schema_name="public",
                perform_setup=False,
                use_jsonb=True
            )
            storage_context = StorageContext.from_defaults(
                vector_store=self._vector_store
            )
            self._index = VectorStoreIndex.from_vector_store(
                vector_store=self._vector_store,
                storage_context=storage_context,
            )

            self._initialized = True
            logger.info(f"RAG service initialized for org: {self.org_id}")

        except Exception as e:
            logger.error(f"Failed to initialize RAG service: {e}", exc_info=True)
            raise

    async def query(self, question: str, top_k: int = 10) -> str:
        """Query the knowledge base and return generated answer
        
        Args:
            question: User's question
            top_k: Number of top results to retrieve
            
        Returns:
            Generated answer based on retrieved context
        """
        if not self._initialized:
            await self.initialize()

        try:
            from llama_index.core.postprocessor import SimilarityPostprocessor
            from llama_index.core.vector_stores import ExactMatchFilter, MetadataFilters
            filters = MetadataFilters(
                filters=[
                    ExactMatchFilter(key="org_id", value=self.org_id)
                ]
            )
            query_engine = self._index.as_query_engine(
                similarity_top_k=top_k,
                response_mode="compact",
                filters=filters,
                node_postprocessors=[
                    SimilarityPostprocessor(similarity_cutoff=0.3)
                ]
            )
            response = query_engine.query(question)

            text = str(response).strip()
            if text:
                return text
            try:
                retriever = self._index.as_retriever(similarity_top_k=top_k)
                nodes = await retriever.aretrieve(question)
                if not nodes:
                    return ""
                qe = self._index.as_query_engine(
                    similarity_top_k=top_k,
                    response_mode="compact",
                    node_postprocessors=[
                        SimilarityPostprocessor(similarity_cutoff=0.3)
                    ],
                )
                fallback_resp = qe.query(question)
                return str(fallback_resp)
            except Exception:
                return ""

        except Exception as e:
            logger.error(f"RAG query failed: {e}", exc_info=True)
            try:
                openai_key = os.getenv("OPENAI_API_KEY")
                if openai_key:
                    from llama_index.llms.openai import OpenAI
                    Settings.llm = OpenAI(model="gpt-4o-mini", api_key=openai_key)
                    from llama_index.core.postprocessor import SimilarityPostprocessor
                    from llama_index.core.vector_stores import (
                        ExactMatchFilter,
                        MetadataFilters,
                    )
                    filters = MetadataFilters(
                        filters=[ExactMatchFilter(key="org_id", value=self.org_id)]
                    )
                    qe = self._index.as_query_engine(
                        similarity_top_k=top_k,
                        response_mode="compact",
                        filters=filters,
                        node_postprocessors=[SimilarityPostprocessor(similarity_cutoff=0.5)],
                    )
                    resp = qe.query(question)
                    resp_text = str(resp).strip()
                    if resp_text:
                        return resp_text
            except Exception as e2:
                logger.warning(f"OpenAI fallback failed: {e2}")
            try:
                fallback_context = await self.get_context_only(question, top_k=top_k, min_similarity=0.1)
                if fallback_context:
                    return fallback_context
            except Exception:
                pass
            return ""

    async def get_context_only(self, question: str, top_k: int = 5, min_similarity: float = 0.7) -> str:
        """Retrieve relevant context without LLM generation
        
        Args:
            question: User's question
            top_k: Number of top results to retrieve
            min_similarity: Minimum similarity threshold (0-1)
            
        Returns:
            Formatted context from retrieved documents
        """
        if not self._initialized:
            await self.initialize()

        try:
            from llama_index.core.vector_stores import ExactMatchFilter, MetadataFilters

            # 1. Strict Multi-tenant Filter
            filters = MetadataFilters(
                filters=[
                    ExactMatchFilter(key="org_id", value=self.org_id)
                ]
            )
            retriever = self._index.as_retriever(
                similarity_top_k=top_k,
                filters=filters
            )
            nodes = await retriever.aretrieve(question)
            filtered_nodes = []
            for node in nodes:
                if hasattr(node, 'score') and node.score is not None:
                    if node.score < min_similarity:
                        continue

                filtered_nodes.append(node)

            if not filtered_nodes:
                logger.info(f"No relevant context found for query (org: {self.org_id})")
                return ""
            context_parts = []
            for idx, node in enumerate(filtered_nodes, 1):
                metadata = node.node.metadata
                source_name = metadata.get('name') or metadata.get('source') or 'Unknown'
                description = metadata.get('description')

                context_parts.append(f"Source {idx}: {source_name}")
                if description:
                    context_parts.append(f"Description: {description}")
                context_parts.append(f"Content: {node.node.text}")
                context_parts.append("")  # Empty line between sources

            context = "\n".join(context_parts)
            logger.info(f"Retrieved {len(filtered_nodes)} relevant documents ({len(context)} chars)")

            return context

        except Exception as e:
            logger.error(f"Context retrieval failed: {e}", exc_info=True)
            return ""

    async def aclose(self):
        """Clean up resources"""
        try:
            if self._vector_store:
                if hasattr(self._vector_store, 'close'):
                    await self._vector_store.close()
            self._initialized = False
            logger.info(f"RAG service closed for org: {self.org_id}")
        except Exception as e:
            logger.error(f"Error closing RAG service: {e}", exc_info=True)


_rag_services: dict[str, LiveKitRAGService] = {}


def get_rag_service(org_id: str) -> LiveKitRAGService:
    """Get or create RAG service for organization
    
    Args:
        org_id: Organization ID
        
    Returns:
        RAG service instance (cached per org)
    """
    if not org_id:
        raise ValueError("org_id is required")

    if org_id not in _rag_services:
        _rag_services[org_id] = LiveKitRAGService(org_id)
        logger.info(f"Created new RAG service for org: {org_id}")

    return _rag_services[org_id]


async def clear_rag_cache():
    """Clear all cached RAG services"""
    global _rag_services
    for service in _rag_services.values():
        await service.aclose()

    _rag_services.clear()
    logger.info("Cleared RAG service cache")
