import logging
from collections.abc import Awaitable
from typing import Any, Callable, Dict, Optional, Tuple

import jsonschema

from .custom_function_executor import CustomFunctionExecutor
from .tool_config_loader import get_custom_function_config

logger = logging.getLogger("custom_function_dispatcher")


class CustomFunctionDispatcher:

    _instance: Optional["CustomFunctionDispatcher"] = None

    def __init__(self):
        self.executor = CustomFunctionExecutor()
        self.handlers: Dict[str, Callable[[Dict, Any, Dict], Awaitable[Dict]]] = {}

    @classmethod
    def get_instance(cls) -> "CustomFunctionDispatcher":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register_handler( self, operation: str, handler: Callable[[Dict[str, Any], Any, Dict[str, Any]], Awaitable[Dict[str, Any]]] ):

        self.handlers[operation] = handler
        logger.info(f"Registered custom handler for operation: {operation}")

    async def _validate_args( self, args: Dict[str, Any], schema: Dict[str, Any] ) -> Tuple[bool, Optional[str]]:

        try:
            jsonschema.validate(instance=args, schema=schema)
            return True, None
        except jsonschema.ValidationError as e:
            error_msg = f"Validation error: {e.message}"
            if e.path:
                error_msg += f" at path: {'.'.join(str(p) for p in e.path)}"
            return False, error_msg
        except Exception as e:
            return False, f"Validation error: {e!s}"

    async def _default_handler( self, args: Dict[str, Any], context: Optional[Any], config: Dict[str, Any] ) -> Dict[str, Any]:
        speak_during_execution = config.get("speakDuringExecution", False)
        speak_after_execution = config.get("speakAfterExecution", True)
        timeout_ms = config.get("timeout", 120000)

        if speak_during_execution and timeout_ms > 2000 and context:
            try:
                if hasattr(context, "session") and context.session:
                    await context.session.generate_reply( instructions="Let me check that for you.", allow_interruptions=True )
            except Exception as e:
                logger.warning(f"Failed to speak during execution: {e}")

        result = await self.executor.execute(config, args, context)

        if not speak_after_execution:
            logger.info("Silent execution mode - result will not be processed by LLM")
            return result

        return result

    async def dispatch( self, operation: str, args: Dict[str, Any], org_id: str, assistant_id: str, context: Optional[Any] = None ) -> Dict[str, Any]:
        logger.info("")
        logger.info("=" * 80)
        logger.info("🚀 CUSTOM FUNCTION DISPATCHER - STARTING")
        logger.info("=" * 80)
        logger.info(f"📞 Operation: '{operation}'")
        logger.info(f"🏢 Org ID: {org_id}")
        logger.info(f"🤖 Assistant ID: {assistant_id}")
        logger.info(f"📝 Args: {args}")

        logger.info("")
        logger.info("📥 LOADING CONFIG FROM DATABASE...")
        config = await get_custom_function_config(operation, org_id, assistant_id)
        if not config:
            error_msg = f"Custom function '{operation}' not found or not enabled for org_id={org_id}, assistant_id={assistant_id}"
            logger.error("")
            logger.error("=" * 80)
            logger.error("❌ DISPATCH FAILED - CONFIG NOT FOUND")
            logger.error(f"   • {error_msg}")
            logger.error("=" * 80)
            return { "success": False, "error": error_msg, "data": None, "responseVariables": {} }

        logger.info("   ✓ Config loaded successfully")
        logger.info(f"   • Config keys: {list(config.keys())}")

        parameters_schema = config.get("parameters")
        if parameters_schema:
            logger.info("")
            logger.info("✅ VALIDATING ARGUMENTS...")
            logger.info(f"   • Schema: {parameters_schema}")
            logger.info(f"   • Args to validate: {args}")
            is_valid, error_msg = await self._validate_args(args, parameters_schema)
            if not is_valid:
                logger.error("")
                logger.error("=" * 80)
                logger.error("❌ DISPATCH FAILED - VALIDATION ERROR")
                logger.error(f"   • Operation: {operation}")
                logger.error(f"   • Error: {error_msg}")
                logger.error("=" * 80)
                return { "success": False, "error": error_msg, "data": None, "responseVariables": {} }
            logger.info("   ✓ Validation passed")
        else:
            logger.info("   ℹ️  No parameter schema defined - skipping validation")

        handler = self.handlers.get(operation)
        if handler:
            logger.info("")
            logger.info(f"🔧 USING CUSTOM HANDLER for operation '{operation}'")
            try:
                result = await handler(args, context, config)
                logger.info("")
                logger.info("=" * 80)
                logger.info("✅ DISPATCH COMPLETED (Custom Handler)")
                logger.info("=" * 80)
                return result
            except Exception as e:
                logger.error("")
                logger.error("=" * 80)
                logger.error("❌ DISPATCH FAILED - HANDLER ERROR")
                logger.error(f"   • Operation: {operation}")
                logger.error(f"   • Error: {e!s}")
                logger.error("=" * 80)
                logger.error("", exc_info=True)
                return { "success": False, "error": f"Handler error: {e!s}", "data": None, "responseVariables": {} }

        logger.info("")
        logger.info(f"⚙️  USING DEFAULT HANDLER for operation '{operation}'")
        speak_during = config.get("speakDuringExecution", False)
        speak_after = config.get("speakAfterExecution", True)
        logger.info(f"   • Speak During Execution: {speak_during}")
        logger.info(f"   • Speak After Execution: {speak_after}")

        result = await self._default_handler(args, context, config)

        logger.info("")
        logger.info("=" * 80)
        logger.info("✅ DISPATCH COMPLETED (Default Handler)")
        logger.info(f"   • Success: {result.get('success', False)}")
        if result.get('success'):
            logger.info(f"   • Status Code: {result.get('statusCode', 'N/A')}")
            logger.info(f"   • Response Variables: {result.get('responseVariables', {})}")
        else:
            logger.error(f"   • Error: {result.get('error', 'Unknown error')}")
        logger.info("=" * 80)

        return result


def get_dispatcher() -> CustomFunctionDispatcher:
    return CustomFunctionDispatcher.get_instance()
