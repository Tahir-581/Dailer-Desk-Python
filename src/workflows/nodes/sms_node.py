

import logging
import os
from typing import Any, Dict

import httpx

from ..node_base import WorkflowNode

logger = logging.getLogger("workflow.node.sms")


class SMSNode(WorkflowNode):


    def __init__(self, node_id: str, config: Dict[str, Any]):
        super().__init__(node_id, "sms", config)

        self.content = config.get("content", "")
        self.to_number = config.get("to_number") or config.get("toNumber")
        self.from_number = config.get("from_number") or config.get("fromNumber")
        self.title = config.get("title", "SMS")

        logger.info(f"SMS node {node_id} initialized: content_length={len(self.content)}")

    async def execute(self, context: Dict[str, Any]) -> str:

        session = context.get("session")

        if not session:
            logger.warning("Session not available in SMS node context")


        recipient = self.to_number
        if not recipient:

            sip_metadata = context.get("sip_metadata", {})
            recipient = sip_metadata.get("phone_number") or sip_metadata.get("call_from")

        if not recipient:

            collected_data = context.get("collected_data", {})
            recipient = collected_data.get("phone_number") or collected_data.get("caller_phone")

        if not recipient:
            logger.error(f"No recipient phone number available for SMS node {self.node_id}")
            return "sent-failed"

        if not self.content:
            logger.error(f"No content provided for SMS node {self.node_id}")
            return "sent-failed"


        try:
            success = await self._send_sms(recipient, self.content)

            if success:
                logger.info(f"SMS sent successfully from node {self.node_id} to {recipient[:4]}****")
                return "sent-success"
            else:
                logger.warning(f"SMS failed to send from node {self.node_id}")
                return "sent-failed"

        except Exception as e:
            logger.error(f"Error sending SMS from node {self.node_id}: {e}", exc_info=True)
            return "sent-failed"

    async def _send_sms(self, to_number: str, content: str) -> bool:


        twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
        twilio_token = os.getenv("TWILIO_AUTH_TOKEN")
        twilio_from = self.from_number or os.getenv("TWILIO_PHONE_NUMBER")

        if twilio_sid and twilio_token and twilio_from:
            return await self._send_via_twilio(
                to_number, content, twilio_sid, twilio_token, twilio_from
            )


        sms_webhook = os.getenv("SMS_WEBHOOK_URL")
        if sms_webhook:
            return await self._send_via_webhook(to_number, content, sms_webhook)


        logger.warning(
            "No SMS provider configured. Set TWILIO_* or SMS_WEBHOOK_URL environment variables."
        )


        if os.getenv("DEVELOPMENT_MODE", "false").lower() == "true":
            logger.info(f"[DEV MODE] Would send SMS to {to_number}: {content[:100]}...")
            return True

        return False

    async def _send_via_twilio(
        self,
        to_number: str,
        content: str,
        account_sid: str,
        auth_token: str,
        from_number: str
    ) -> bool:
        try:
            from twilio.rest import Client as TwilioClient
            client = TwilioClient(account_sid, auth_token)
            message = client.messages.create(
                body=content,
                from_=from_number,
                to=to_number
            )
            return message.sid is not None
        except Exception as e:
            logger.error(f"Twilio SMS error: {e}", exc_info=True)
            return False

    async def _send_via_webhook(
        self,
        to_number: str,
        content: str,
        webhook_url: str
    ) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    webhook_url,
                    json={
                        "to": to_number,
                        "from": self.from_number,
                        "content": content,
                        "node_id": self.node_id,
                    },
                    timeout=30.0
                )

                return response.status_code in (200, 201, 202, 204)

        except Exception as e:
            logger.error(f"SMS webhook error: {e}", exc_info=True)
            return False
