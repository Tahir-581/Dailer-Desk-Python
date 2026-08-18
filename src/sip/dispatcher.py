

import logging
import uuid
from typing import Any, Dict, List, Optional

from livekit import api
from livekit.protocol import sip as sip_proto

from .config import (
    DispatchRule,
    DispatchRuleType,
    get_sip_config,
)
from .errors import (
    SIPDispatchError,
    SIPDispatchRuleNotFoundError,
)

logger = logging.getLogger("SIP.Dispatcher")


class SIPDispatcher:

    def __init__(self, livekit_api: Optional[api.LiveKitAPI] = None):
        self._api = livekit_api
        self._config = get_sip_config()

    async def _get_api(self) -> api.LiveKitAPI:
        if self._api:
            return self._api

        self._api = api.LiveKitAPI(
            url=self._config.livekit_url,
            api_key=self._config.livekit_api_key,
            api_secret=self._config.livekit_api_secret,
        )
        return self._api




    async def list_dispatch_rules(self) -> List[DispatchRule]:

        try:
            lk_api = await self._get_api()
            response = await lk_api.sip.list_sip_dispatch_rule(
                sip_proto.ListSIPDispatchRuleRequest()
            )

            rules = []
            for item in response.items:
                rule_type = DispatchRuleType.INDIVIDUAL
                room_prefix = None
                room_name = None
                agent_name = None
                room_config_dict = {}

                if hasattr(item, "rule") and item.rule:
                    rule_type_field = item.rule.WhichOneof("rule")
                    if rule_type_field == "dispatch_rule_individual":
                        rule_type = DispatchRuleType.INDIVIDUAL
                        if item.rule.dispatch_rule_individual:
                            room_prefix = item.rule.dispatch_rule_individual.room_prefix
                    elif rule_type_field == "dispatch_rule_direct":
                        rule_type = DispatchRuleType.DIRECT
                        if item.rule.dispatch_rule_direct:
                            room_name = item.rule.dispatch_rule_direct.room_name

                # Extract room_config and agent_name if present
                if hasattr(item, 'room_config') and item.room_config:
                    if item.room_config.agents:
                        agent_name = item.room_config.agents[0].agent_name
                        room_config_dict = {
                            "agents": [
                                {
                                    "agent_name": agent.agent_name,
                                    "metadata": agent.metadata if agent.metadata else None
                                }
                                for agent in item.room_config.agents
                            ]
                        }
                        logger.debug(f"Found agent '{agent_name}' in dispatch rule '{item.name}'")

                rules.append(
                    DispatchRule(
                        rule_id=item.sip_dispatch_rule_id,
                        name=item.name,
                        rule_type=rule_type,
                        room_prefix=room_prefix,
                        room_name=room_name,
                        trunk_ids=list(item.trunk_ids) if item.trunk_ids else [],
                        inbound_numbers=list(item.inbound_numbers)
                        if item.inbound_numbers
                        else [],
                        agent_name=agent_name,
                        room_config=room_config_dict,
                        metadata={"raw": item.metadata} if item.metadata else {},
                    )
                )

            logger.info(f"Listed {len(rules)} dispatch rules")
            return rules

        except Exception as e:
            logger.error(f"Error listing dispatch rules: {e}")
            raise SIPDispatchError(f"Failed to list dispatch rules: {e}")

    async def get_dispatch_rule(self, rule_id: str) -> DispatchRule:
        rules = await self.list_dispatch_rules()
        for rule in rules:
            if rule.rule_id == rule_id:
                return rule
        raise SIPDispatchRuleNotFoundError()

    async def create_individual_dispatch_rule(
        self,
        name: str,
        room_prefix: str = "call-",
        trunk_ids: Optional[List[str]] = None,
        inbound_numbers: Optional[List[str]] = None,
        agent_name: Optional[str] = None,
        hide_phone_number: bool = False,
        pin: Optional[str] = None,
        attributes: Optional[Dict[str, str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DispatchRule:
        try:
            lk_api = await self._get_api()

            individual_rule = sip_proto.SIPDispatchRuleIndividual(
                room_prefix=room_prefix,
            )
            if pin:
                individual_rule.pin = pin

            rule = sip_proto.SIPDispatchRule()
            rule.dispatch_rule_individual.CopyFrom(individual_rule)

            request = sip_proto.CreateSIPDispatchRuleRequest(
                rule=rule,
                name=name,
                trunk_ids=trunk_ids or [],
                inbound_numbers=inbound_numbers or [],
                hide_phone_number=hide_phone_number,
            )

            # Add room_config with agents if agent_name is provided
            if agent_name:
                from livekit.protocol import room as room_proto
                room_config = room_proto.RoomConfiguration()
                agent_config = room_proto.AgentConfiguration()
                agent_config.agent_name = agent_name
                if metadata:
                    import json
                    agent_config.metadata = json.dumps(metadata)
                room_config.agents.append(agent_config)
                request.room_config.CopyFrom(room_config)
                logger.info(f"Added room_config with agent '{agent_name}' to dispatch rule")

            # Add attributes if provided
            if attributes:
                for key, value in attributes.items():
                    request.attributes[key] = value

            response = await lk_api.sip.create_sip_dispatch_rule(request)

            # Extract room_config and agent_name from response if present
            extracted_agent_name = agent_name
            room_config_dict = {}
            if hasattr(response, 'room_config') and response.room_config:
                if response.room_config.agents:
                    extracted_agent_name = response.room_config.agents[0].agent_name
                    room_config_dict = {
                        "agents": [
                            {
                                "agent_name": agent.agent_name,
                                "metadata": agent.metadata if agent.metadata else None
                            }
                            for agent in response.room_config.agents
                        ]
                    }

            created_rule = DispatchRule(
                rule_id=response.sip_dispatch_rule_id,
                name=response.name,
                rule_type=DispatchRuleType.INDIVIDUAL,
                room_prefix=room_prefix,
                pin=pin,
                trunk_ids=trunk_ids or [],
                inbound_numbers=inbound_numbers or [],
                agent_name=extracted_agent_name,
                hide_phone_number=hide_phone_number,
                attributes=attributes or {},
                room_config=room_config_dict,
                metadata=metadata or {},
            )

            logger.info(f"Created individual dispatch rule: {created_rule.rule_id}")
            return created_rule

        except Exception as e:
            logger.error(f"Error creating dispatch rule: {e}")
            raise SIPDispatchError(f"Failed to create dispatch rule: {e}")

    async def create_direct_dispatch_rule(
        self,
        name: str,
        room_name: str,
        trunk_ids: Optional[List[str]] = None,
        inbound_numbers: Optional[List[str]] = None,
        agent_name: Optional[str] = None,
        hide_phone_number: bool = False,
        pin: Optional[str] = None,
        attributes: Optional[Dict[str, str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DispatchRule:
        try:
            lk_api = await self._get_api()

            direct_rule = sip_proto.SIPDispatchRuleDirect(
                room_name=room_name,
            )
            if pin:
                direct_rule.pin = pin

            rule = sip_proto.SIPDispatchRule()
            rule.dispatch_rule_direct.CopyFrom(direct_rule)

            request = sip_proto.CreateSIPDispatchRuleRequest(
                rule=rule,
                name=name,
                trunk_ids=trunk_ids or [],
                inbound_numbers=inbound_numbers or [],
                hide_phone_number=hide_phone_number,
            )

            # Add room_config with agents if agent_name is provided
            if agent_name:
                from livekit.protocol import room as room_proto
                room_config = room_proto.RoomConfiguration()
                agent_config = room_proto.AgentConfiguration()
                agent_config.agent_name = agent_name
                if metadata:
                    import json
                    agent_config.metadata = json.dumps(metadata)
                room_config.agents.append(agent_config)
                request.room_config.CopyFrom(room_config)
                logger.info(f"Added room_config with agent '{agent_name}' to dispatch rule")

            # Add attributes if provided
            if attributes:
                for key, value in attributes.items():
                    request.attributes[key] = value

            response = await lk_api.sip.create_sip_dispatch_rule(request)

            # Extract room_config and agent_name from response if present
            extracted_agent_name = agent_name
            room_config_dict = {}
            if hasattr(response, 'room_config') and response.room_config:
                if response.room_config.agents:
                    extracted_agent_name = response.room_config.agents[0].agent_name
                    room_config_dict = {
                        "agents": [
                            {
                                "agent_name": agent.agent_name,
                                "metadata": agent.metadata if agent.metadata else None
                            }
                            for agent in response.room_config.agents
                        ]
                    }

            created_rule = DispatchRule(
                rule_id=response.sip_dispatch_rule_id,
                name=response.name,
                rule_type=DispatchRuleType.DIRECT,
                room_name=room_name,
                pin=pin,
                trunk_ids=trunk_ids or [],
                inbound_numbers=inbound_numbers or [],
                agent_name=extracted_agent_name,
                hide_phone_number=hide_phone_number,
                attributes=attributes or {},
                room_config=room_config_dict,
                metadata=metadata or {},
            )

            logger.info(f"Created direct dispatch rule: {created_rule.rule_id}")
            return created_rule

        except Exception as e:
            logger.error(f"Error creating direct dispatch rule: {e}")
            raise SIPDispatchError(f"Failed to create direct dispatch rule: {e}")

    async def delete_dispatch_rule(self, rule_id: str) -> None:
        try:
            lk_api = await self._get_api()
            request = sip_proto.DeleteSIPDispatchRuleRequest(
                sip_dispatch_rule_id=rule_id
            )
            await lk_api.sip.delete_sip_dispatch_rule(request)
            logger.info(f"Deleted dispatch rule: {rule_id}")
        except Exception as e:
            logger.error(f"Error deleting dispatch rule: {e}")
            raise SIPDispatchError(f"Failed to delete dispatch rule: {e}")





    async def find_rule_for_number(self, phone_number: str) -> Optional[DispatchRule]:

        rules = await self.list_dispatch_rules()

        for rule in rules:

            if phone_number in rule.inbound_numbers:
                return rule

            if not rule.inbound_numbers:
                return rule

        return None

    def generate_room_name(
        self, rule: DispatchRule, call_id: Optional[str] = None
    ) -> str:
        if rule.rule_type == DispatchRuleType.DIRECT:
            if not rule.room_name:
                raise SIPDispatchError(
                    f"Direct dispatch rule '{rule.name}' (ID: {rule.rule_id}) "
                    f"must have a room_name specified"
                )
            return rule.room_name

        prefix = rule.room_prefix or "call-"
        unique_id = call_id or uuid.uuid4().hex[:12]
        return f"{prefix}{unique_id}"



_dispatcher: Optional[SIPDispatcher] = None


def get_dispatcher() -> SIPDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = SIPDispatcher()
    return _dispatcher
