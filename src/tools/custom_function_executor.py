import logging
import re
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("custom_function_executor")


class CustomFunctionExecutor:
    def __init__(self):
        pass

    def _extract_value_by_path(self, data: Any, path: str) -> Any:

        if not path:
            return None

        try:
            parts = path.split(".")
            current = data

            for part in parts:
                if isinstance(current, dict):
                    current = current.get(part)
                elif isinstance(current, list):
                    try:
                        index = int(part)
                        if 0 <= index < len(current):
                            current = current[index]
                        else:
                            return None
                    except (ValueError, IndexError):
                        return None
                else:
                    return None

                if current is None:
                    return None

            return current
        except Exception as e:
            logger.warning(f"Error extracting path '{path}': {e}")
            return None

    def _extract_response_variables( self, response_data: Dict[str, Any], response_variables: Dict[str, str] ) -> Dict[str, Any]:
        extracted = {}
        for var_name, json_path in response_variables.items():
            value = self._extract_value_by_path(response_data, json_path)
            extracted[var_name] = value
            logger.debug(f"Extracted {var_name} = {value} from path {json_path}")
        return extracted

    def _resolve_variable(self, var_name: str, context: Optional[Any]) -> Optional[str]:

        if not context:
            return None


        workflow_engine = None
        if hasattr(context, "session") and context.session:
            try:
                if hasattr(context.session, "userdata"):
                    workflow_engine = context.session.userdata.get("workflow_engine")
            except Exception as e:
                logger.debug(f"Could not access session.userdata: {e}")


        if workflow_engine and hasattr(workflow_engine, "state"):
            collected_data = getattr(workflow_engine.state, "collected_data", {})
            if isinstance(collected_data, dict) and var_name in collected_data:
                value = collected_data[var_name]
                logger.debug(f"Resolved variable '{var_name}' from workflow collected_data: {value}")
                return str(value) if value is not None else None


        if hasattr(context, "session") and context.session:
            try:
                if hasattr(context.session, "userdata"):
                    userdata = context.session.userdata
                    if isinstance(userdata, dict) and var_name in userdata:
                        value = userdata[var_name]
                        logger.debug(f"Resolved variable '{var_name}' from session userdata: {value}")
                        return str(value) if value is not None else None
            except Exception as e:
                logger.debug(f"Could not access session.userdata for variable resolution: {e}")



        if hasattr(context, "response_variables"):
            response_vars = context.response_variables
            if isinstance(response_vars, dict) and var_name in response_vars:
                value = response_vars[var_name]
                logger.debug(f"Resolved variable '{var_name}' from response variables: {value}")
                return str(value) if value is not None else None

        logger.debug(f"Variable '{var_name}' not found in any context source")
        return None

    def _resolve_variables_in_string(self, text: str, context: Optional[Any]) -> str:

        if not isinstance(text, str):
            return text


        pattern = r'\{\{(\w+)\}\}'

        def replace_match(match):
            var_name = match.group(1)
            resolved_value = self._resolve_variable(var_name, context)

            if resolved_value is not None:
                logger.debug(f"Replaced '{{{{{var_name}}}}}' with '{resolved_value}'")
                return resolved_value
            else:
                logger.warning(f"Variable '{{{{{var_name}}}}}' not found in context, leaving placeholder as-is")
                return match.group(0)

        resolved_text = re.sub(pattern, replace_match, text)
        return resolved_text

    def _resolve_headers(self, headers: Dict[str, Any], context: Optional[Any]) -> Dict[str, Any]:

        if not headers:
            return headers

        resolved_headers = {}
        for key, value in headers.items():
            if isinstance(value, str):
                resolved_headers[key] = self._resolve_variables_in_string(value, context)
            else:
                resolved_headers[key] = value

        return resolved_headers

    def _resolve_query_params(self, query_params: Dict[str, Any], context: Optional[Any]) -> Dict[str, Any]:

        if not query_params:
            return query_params

        resolved_params = {}
        for key, value in query_params.items():
            if isinstance(value, str):
                resolved_params[key] = self._resolve_variables_in_string(value, context)
            elif isinstance(value, (list, tuple)):

                resolved_list = []
                for item in value:
                    if isinstance(item, str):
                        resolved_list.append(self._resolve_variables_in_string(item, context))
                    else:
                        resolved_list.append(item)
                resolved_params[key] = resolved_list
            else:
                resolved_params[key] = value

        return resolved_params

    async def _make_request( self, client: httpx.AsyncClient, method: str, endpoint: str, headers: Dict[str, Any], query_params: Dict[str, Any], payload: Optional[Dict[str, Any]], payload_format: str ) -> httpx.Response:
        """
        Internal helper to perform an HTTP request using httpx.
        """

        try:
            request_kwargs: Dict[str, Any] = {
                "method": method.upper(),
                "url": endpoint,
                "headers": headers or None,
                "params": query_params or None,
            }

            if payload is not None:
                if payload_format == "form":
                    request_kwargs["data"] = payload
                else:
                    request_kwargs["json"] = payload

            logger.debug(f"Executing HTTP request: {request_kwargs}")
            response = await client.request(**request_kwargs)
            return response

        except Exception as e:
            logger.error(f"Error while making HTTP request to {endpoint}: {e}", exc_info=True)
            raise

    async def execute( self, config: Dict[str, Any], args: Dict[str, Any], context: Optional[Any] = None ) -> Dict[str, Any]:
        """
        Execute a custom function HTTP call based on the config loaded from the API.
        Returns a standardized result dict expected by the dispatcher.
        """

        try:
            method = (config.get("method") or "GET").upper()
            endpoint = config.get("endpoint")
            base_url = (config.get("baseUrl") or "").rstrip("/")
            payload_format = config.get("payloadFormat", "json")
            timeout_ms = config.get("timeout", 120000)

            if not endpoint:
                error_msg = "Custom function config is missing 'endpoint'"
                logger.error(error_msg)
                return { "success": False, "error": error_msg, "data": None, "responseVariables": {} }

            # Build full URL if a base URL is provided and endpoint is relative
            if base_url and not endpoint.startswith("http://") and not endpoint.startswith("https://"):
                url = f"{base_url}/{endpoint.lstrip('/')}"
            else:
                url = endpoint

            logger.info(f"Executing custom function HTTP call: {method} {url}")

            raw_headers = config.get("headers", {}) or {}
            raw_query = config.get("queryParams", {}) or {}

            headers = self._resolve_headers(raw_headers, context)
            query_params = self._resolve_query_params(raw_query, context)

            # For now, pass the provided args as the payload body directly.
            payload = args or None

            timeout_seconds = max(1.0, float(timeout_ms) / 1000.0) if isinstance(timeout_ms, (int, float)) else 120.0

            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await self._make_request(
                    client=client,
                    method=method,
                    endpoint=url,
                    headers=headers,
                    query_params=query_params,
                    payload=payload,
                    payload_format=payload_format,
                )

            # Try to parse JSON, otherwise fall back to plain text
            response_data: Any
            try:
                response_data = response.json()
            except ValueError:
                response_data = response.text

            response_variables_config = config.get("responseVariables") or {}
            response_variables: Dict[str, Any] = {}
            if isinstance(response_data, dict) and response_variables_config:
                response_variables = self._extract_response_variables(
                    response_data,
                    response_variables_config,
                )

            result = {
                "success": response.is_success,
                "statusCode": response.status_code,
                "data": response_data,
                "responseVariables": response_variables,
            }

            if not response.is_success:
                logger.warning(f"Custom function HTTP call returned non-success status {response.status_code}")

            return result

        except httpx.TimeoutException:
            error_msg = "Custom function HTTP request timed out"
            logger.error(error_msg)
            return { "success": False, "error": error_msg, "data": None, "responseVariables": {} }

        except httpx.RequestError as e:
            error_msg = f"Custom function HTTP request error: {e}"
            logger.error(error_msg)
            return { "success": False, "error": error_msg, "data": None, "responseVariables": {} }

        except Exception as e:
            error_msg = f"Unexpected error executing custom function: {e}"
            logger.error(error_msg, exc_info=True)
            return { "success": False, "error": error_msg, "data": None, "responseVariables": {} }
