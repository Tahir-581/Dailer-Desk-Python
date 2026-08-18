import asyncio
import logging

from livekit.agents import ToolError, get_job_context
from livekit.agents.llm import function_tool
from livekit.agents.voice import RunContext

from rag_service import get_rag_service

logger = logging.getLogger("rag_tool")

@function_tool()
async def search_knowledge_base(
    context: RunContext,
    query: str
) -> str:
    """Search the company knowledge base for specific information about products, policies, or services.
    Use this tool when you need factual information that you don't already have in your instructions.
    
    Args:
        query: The search query or question to look up.
    """
    # Initialize variables early to avoid UnboundLocalError
    thinking_sounds = None
    org_id = None

    try:
        # 1. Get org_id from participant metadata (Multi-tenancy)
        # We try to get it from the session userdata first, then room metadata

        # Check session userdata (handle case where userdata is not set)
        try:
            if context.session.userdata and "org_id" in context.session.userdata:
                org_id = context.session.userdata["org_id"]
                logger.info(f"RAG Tool: Found org_id in session userdata: {org_id}")
        except (ValueError, AttributeError) as e:
            logger.warning(f"RAG Tool: Could not access session userdata: {e}")

        # Fallback: Search participants
        if not org_id:
            from .call_tools import _get_org_and_assistant_ids
            # Try to get room from job context if session doesn't have it
            room = None
            if hasattr(context.session, 'room'):
                room = context.session.room
            else:
                try:
                    job_ctx = get_job_context()
                    room = job_ctx.room
                except Exception as e:
                    logger.warning(f"RAG Tool: Could not get room from job context for org_id: {e}")

            if room:
                org_id, _ = _get_org_and_assistant_ids(room)
                if org_id:
                    logger.info(f"RAG Tool: Found org_id from participant metadata: {org_id}")
            else:
                logger.warning("RAG Tool: No room available for org_id extraction")

        if not org_id:
            logger.error("RAG Tool: No org_id found. Cannot perform isolated search.")
            raise ToolError("Organization context missing. Search unavailable.")

        # 2. Latency Management: Filler Speech
        # We start a background task to say something while we wait for LlamaIndex
        # This prevents awkward silence
        filler_phrases = [
            "Let me check that in our records for you...",
            "One moment while I look that up...",
            "I'm searching our internal documents to get you the most accurate answer...",
            "Let me just verify those details for you..."
        ]
        import random
        filler_text = random.choice(filler_phrases)

        logger.info(f"RAG Tool: Searching KB for org {org_id} with query: {query}")

        # We use session.say for filler speech
        # allow_interruptions=True is important here
        await context.session.say(filler_text, allow_interruptions=True)

        # 3. Perform the query via RAG Service
        try:
            rag_service = get_rag_service(org_id)
            logger.info(f"RAG Tool: Successfully initialized RAG service for org {org_id}")
        except Exception as e:
            logger.error(f"RAG Tool: Failed to initialize RAG service for org {org_id}: {e}")
            raise ToolError(f"Knowledge base unavailable for organization {org_id}")

        # We use a timeout to ensure we don't block forever
        try:
            # query() returns the synthesized response from LlamaIndex
            # Increase top_k for better coverage and reduce similarity threshold
            logger.info("RAG Tool: Executing query with top_k=10, timeout=12s")
            response_text = await asyncio.wait_for(rag_service.query(query, top_k=10), timeout=12.0)

            if not response_text or response_text.strip() == "":
                logger.info(f"RAG Tool: No synthesized answer found for query: {query}")
                # Try multiple fallback strategies

                # Strategy 1: Try with lower similarity threshold
                try:
                    ctx_only = await asyncio.wait_for(rag_service.get_context_only(query, top_k=15, min_similarity=0.2), timeout=6.0)
                    if ctx_only and ctx_only.strip():
                        # Format context for better LLM consumption
                        formatted_context = f"Based on our records:\n{ctx_only}"
                        logger.info(f"RAG Tool: Returning formatted context with low threshold ({len(formatted_context)} chars)")
                        return formatted_context
                except Exception as e:
                    logger.warning(f"RAG Tool: Low threshold context retrieval failed: {e}")

                # Strategy 2: Try broader search with related terms
                try:
                    # Expand query with related terms for payment/account questions
                    expanded_query = query
                    if any(term in query.lower() for term in ['payment', 'paid', 'account', 'cursor']):
                        expanded_query = f"{query} payment billing subscription account invoice receipt"

                    ctx_expanded = await asyncio.wait_for(rag_service.get_context_only(expanded_query, top_k=20, min_similarity=0.15), timeout=6.0)
                    if ctx_expanded and ctx_expanded.strip():
                        formatted_context = f"Based on our billing and account records:\n{ctx_expanded}"
                        logger.info(f"RAG Tool: Returning expanded context ({len(formatted_context)} chars)")
                        return formatted_context
                except Exception as e:
                    logger.warning(f"RAG Tool: Expanded context retrieval failed: {e}")

                return "I couldn't find any specific information about that in our records."

            logger.info(f"RAG Tool: Retrieved response of length {len(response_text)}")
            return response_text

        except asyncio.TimeoutError:
            logger.warning(f"RAG Tool: Query timed out for org {org_id}")
            return "I'm sorry, I'm having trouble accessing our records at the moment. Please try again in a bit."

    except Exception as e:
        logger.error(f"RAG Tool: Unexpected error: {e}", exc_info=True)
        raise ToolError(f"Search failed: {e!s}")

    finally:
        pass
