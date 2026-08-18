

import logging
from typing import Any, Dict, List, Optional

from livekit import api
from livekit.protocol import sip as sip_proto

from .config import (
    InboundTrunkConfig,
    OutboundTrunkConfig,
    SIPTransport,
    get_sip_config,
    normalize_phone_number,
    validate_country_code,
    validate_e164_phone_number,
)
from .errors import (
    SIPTrunkConfigurationError,
    SIPTrunkError,
    SIPTrunkNotFoundError,
)

logger = logging.getLogger("SIP.TrunkManager")


class SIPTrunkManager:


    def __init__(self, livekit_api: Optional[api.LiveKitAPI] = None):

        self._api = livekit_api
        self._config = get_sip_config()

    async def _get_api(self) -> api.LiveKitAPI:

        if self._api:
            return self._api

        # Create new API instance if none exists
        self._api = api.LiveKitAPI(
            url=self._config.livekit_url,
            api_key=self._config.livekit_api_key,
            api_secret=self._config.livekit_api_secret
        )
        return self._api





    async def list_inbound_trunks(self) -> List[InboundTrunkConfig]:

        try:
            lk_api = await self._get_api()
            response = await lk_api.sip.list_sip_inbound_trunk(
                sip_proto.ListSIPInboundTrunkRequest()
            )

            trunks = []
            for trunk in response.items:
                trunks.append(
                    InboundTrunkConfig(
                        trunk_id=trunk.sip_trunk_id,
                        name=trunk.name,
                        numbers=list(trunk.numbers),
                        allowed_source_ips=list(trunk.allowed_addresses)
                        if trunk.allowed_addresses
                        else [],
                        allowed_numbers=list(trunk.allowed_numbers)
                        if hasattr(trunk, 'allowed_numbers') and trunk.allowed_numbers
                        else [],
                        auth_username=trunk.auth_username
                        if trunk.auth_username
                        else None,
                        auth_password=trunk.auth_password
                        if trunk.auth_password
                        else None,
                        krisp_enabled=getattr(trunk, 'krisp_enabled', False),
                        metadata={},
                    )
                )

            logger.info(f"Listed {len(trunks)} inbound trunks")
            return trunks

        except Exception as e:
            logger.error(f"Error listing inbound trunks: {e}")
            raise SIPTrunkError(f"Failed to list inbound trunks: {e}")

    async def get_inbound_trunk(self, trunk_id: str) -> InboundTrunkConfig:

        trunks = await self.list_inbound_trunks()
        for trunk in trunks:
            if trunk.trunk_id == trunk_id:
                return trunk
        raise SIPTrunkNotFoundError(trunk_id)

    async def create_inbound_trunk(
        self,
        name: str,
        numbers: List[str],
        allowed_source_ips: Optional[List[str]] = None,
        allowed_numbers: Optional[List[str]] = None,
        auth_username: Optional[str] = None,
        auth_password: Optional[str] = None,
        krisp_enabled: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
        validate_numbers: bool = True,
    ) -> InboundTrunkConfig:

        try:

            normalized_numbers = []
            if validate_numbers:
                for num in numbers:
                    is_valid, error = validate_e164_phone_number(num)
                    if not is_valid:

                        try:
                            normalized = normalize_phone_number(num)

                            normalized_numbers.append(normalized.replace("tel:", ""))
                        except ValueError:
                            raise SIPTrunkError(
                                f"Invalid phone number '{num}': {error}. "
                                f"Expected E.164 format (e.g., +14155551234)"
                            )
                    else:

                        cleaned = num.replace("tel:", "")
                        normalized_numbers.append(cleaned)
            else:
                normalized_numbers = [n.replace("tel:", "") for n in numbers]

            lk_api = await self._get_api()

            trunk_info = sip_proto.SIPInboundTrunkInfo(
                name=name,
                numbers=normalized_numbers,
                allowed_addresses=allowed_source_ips or [],
                allowed_numbers=allowed_numbers or [],
                auth_username=auth_username or "",
                auth_password=auth_password or "",
                krisp_enabled=krisp_enabled,
            )

            request = sip_proto.CreateSIPInboundTrunkRequest(trunk=trunk_info)
            response = await lk_api.sip.create_sip_inbound_trunk(request)

            created_trunk = InboundTrunkConfig(
                trunk_id=response.sip_trunk_id,
                name=response.name,
                numbers=list(response.numbers),
                allowed_source_ips=list(response.allowed_addresses)
                if response.allowed_addresses
                else [],
                allowed_numbers=list(response.allowed_numbers)
                if hasattr(response, 'allowed_numbers') and response.allowed_numbers
                else [],
                auth_username=response.auth_username
                if response.auth_username
                else None,
                auth_password=response.auth_password
                if response.auth_password
                else None,
                krisp_enabled=getattr(response, 'krisp_enabled', krisp_enabled),
                metadata=metadata or {},
            )

            logger.info(f"Created inbound trunk: {created_trunk.trunk_id}")
            return created_trunk

        except Exception as e:
            logger.error(f"Error creating inbound trunk: {e}")
            raise SIPTrunkError(f"Failed to create inbound trunk: {e}")

    async def update_inbound_trunk(
        self,
        trunk_id: str,
        name: Optional[str] = None,
        numbers: Optional[List[str]] = None,
        allowed_source_ips: Optional[List[str]] = None,
        auth_username: Optional[str] = None,
        auth_password: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        validate_numbers: bool = True,
    ) -> InboundTrunkConfig:

        try:

            existing_trunk = await self.get_inbound_trunk(trunk_id)

            # Build updated trunk info from existing + updates
            updated_name = name if name is not None else existing_trunk.name
            updated_numbers = numbers if numbers is not None else existing_trunk.numbers
            updated_allowed_ips = allowed_source_ips if allowed_source_ips is not None else existing_trunk.allowed_source_ips
            updated_auth_username = auth_username if auth_username is not None else (existing_trunk.auth_username or "")
            updated_auth_password = auth_password if auth_password is not None else (existing_trunk.auth_password or "")
            updated_metadata = metadata if metadata is not None else existing_trunk.metadata

            # Normalize numbers if provided
            if numbers is not None:
                normalized_numbers = []
                if validate_numbers:
                    for num in numbers:
                        is_valid, error = validate_e164_phone_number(num)
                        if not is_valid:
                            try:
                                normalized = normalize_phone_number(num)
                                normalized_numbers.append(normalized.replace("tel:", ""))
                            except ValueError:
                                raise SIPTrunkError(
                                    f"Invalid phone number '{num}': {error}. "
                                    f"Expected E.164 format (e.g., +14155551234)"
                                )
                        else:
                            normalized_numbers.append(num.replace("tel:", ""))
                else:
                    normalized_numbers = [n.replace("tel:", "") for n in numbers]
                updated_numbers = normalized_numbers

            lk_api = await self._get_api()

            # Build SIPInboundTrunkInfo with all fields
            trunk_info = sip_proto.SIPInboundTrunkInfo(
                name=updated_name,
                numbers=updated_numbers,
                allowed_addresses=updated_allowed_ips or [],
                auth_username=updated_auth_username or "",
                auth_password=updated_auth_password or "",
            )

            # Call update API - it takes trunk_id and trunk info
            response = await lk_api.sip.update_sip_inbound_trunk(trunk_id, trunk_info)


            updated_trunk = InboundTrunkConfig(
                trunk_id=response.sip_trunk_id,
                name=response.name,
                numbers=list(response.numbers),
                allowed_source_ips=list(response.allowed_addresses)
                if response.allowed_addresses
                else [],
                auth_username=response.auth_username
                if response.auth_username
                else None,
                auth_password=response.auth_password
                if response.auth_password
                else None,
                metadata=metadata if metadata is not None else existing_trunk.metadata,
            )

            logger.info(f"Updated inbound trunk: {updated_trunk.trunk_id}")
            return updated_trunk

        except SIPTrunkNotFoundError:
            raise
        except SIPTrunkConfigurationError:
            raise
        except Exception as e:
            logger.error(f"Error updating inbound trunk {trunk_id}: {e}")
            raise SIPTrunkError(f"Failed to update inbound trunk: {e}")

    async def delete_inbound_trunk(self, trunk_id: str) -> None:

        try:
            lk_api = await self._get_api()
            request = sip_proto.DeleteSIPTrunkRequest(sip_trunk_id=trunk_id)
            await lk_api.sip.delete_sip_trunk(request)
            logger.info(f"Deleted inbound trunk: {trunk_id}")
        except Exception as e:
            logger.error(f"Error deleting inbound trunk {trunk_id}: {e}")
            raise SIPTrunkError(f"Failed to delete inbound trunk: {e}")





    async def list_outbound_trunks(self) -> List[OutboundTrunkConfig]:

        try:
            lk_api = await self._get_api()
            response = await lk_api.sip.list_sip_outbound_trunk(
                sip_proto.ListSIPOutboundTrunkRequest()
            )

            trunks = []
            for trunk in response.items:
                transport = SIPTransport.AUTO
                if trunk.transport:
                    transport = SIPTransport(trunk.transport)

                trunks.append(
                    OutboundTrunkConfig(
                        trunk_id=trunk.sip_trunk_id,
                        name=trunk.name,
                        address=trunk.address,
                        numbers=list(trunk.numbers),
                        auth_username=trunk.auth_username or "",
                        auth_password=trunk.auth_password or "",
                        transport=transport,
                        destination_country=trunk.destination_country if trunk.destination_country else None,
                    )
                )

            logger.info(f"Listed {len(trunks)} outbound trunks")
            return trunks

        except Exception as e:
            logger.error(f"Error listing outbound trunks: {e}")
            raise SIPTrunkError(f"Failed to list outbound trunks: {e}")

    async def get_outbound_trunk(self, trunk_id: str) -> OutboundTrunkConfig:

        trunks = await self.list_outbound_trunks()
        for trunk in trunks:
            if trunk.trunk_id == trunk_id:
                return trunk
        raise SIPTrunkNotFoundError(trunk_id)

    async def create_outbound_trunk(
        self,
        name: str,
        address: str,
        numbers: List[str],
        auth_username: str,
        auth_password: str,
        transport: SIPTransport = SIPTransport.AUTO,
        destination_country: Optional[str] = None,
        headers_to_attributes: Optional[Dict[str, str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        validate_numbers: bool = True,
    ) -> OutboundTrunkConfig:

        try:

            normalized_numbers = []
            if validate_numbers:
                for num in numbers:
                    is_valid, error = validate_e164_phone_number(num)
                    if not is_valid:

                        try:
                            normalized = normalize_phone_number(num)

                            normalized_numbers.append(normalized.replace("tel:", ""))
                        except ValueError:
                            raise SIPTrunkError(
                                f"Invalid phone number '{num}': {error}. "
                                f"Expected E.164 format (e.g., +14155551234)"
                            )
                    else:

                        cleaned = num.replace("tel:", "")
                        normalized_numbers.append(cleaned)
            else:
                normalized_numbers = [n.replace("tel:", "") for n in numbers]


            if destination_country:
                is_valid, error = validate_country_code(destination_country)
                if not is_valid:
                    raise SIPTrunkConfigurationError(
                        f"Invalid destination_country: {error}"
                    )

            lk_api = await self._get_api()

            trunk_info = sip_proto.SIPOutboundTrunkInfo(
                name=name,
                address=address,
                numbers=normalized_numbers,
                auth_username=auth_username,
                auth_password=auth_password,
                transport=transport.value if isinstance(transport, SIPTransport) else transport,
                destination_country=destination_country or "",
            )
            if headers_to_attributes:
                for k, v in headers_to_attributes.items():
                    trunk_info.headers_to_attributes[k] = v

            request = sip_proto.CreateSIPOutboundTrunkRequest(trunk=trunk_info)
            response = await lk_api.sip.create_sip_outbound_trunk(request)

            created_trunk = OutboundTrunkConfig(
                trunk_id=response.sip_trunk_id,
                name=response.name,
                address=response.address,
                numbers=list(response.numbers),
                auth_username=response.auth_username or "",
                auth_password=response.auth_password or "",
                transport=transport,
                destination_country=destination_country,
                metadata=metadata or {},
            )

            logger.info(f"Created outbound trunk: {created_trunk.trunk_id}")
            return created_trunk

        except Exception as e:
            logger.error(f"Error creating outbound trunk: {e}")
            raise SIPTrunkError(f"Failed to create outbound trunk: {e}")

    async def update_outbound_trunk(
        self,
        trunk_id: str,
        name: Optional[str] = None,
        address: Optional[str] = None,
        numbers: Optional[List[str]] = None,
        auth_username: Optional[str] = None,
        auth_password: Optional[str] = None,
        transport: Optional[SIPTransport] = None,
        destination_country: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        validate_numbers: bool = True,
    ) -> OutboundTrunkConfig:

        try:

            existing_trunk = await self.get_outbound_trunk(trunk_id)


            if destination_country is not None:
                is_valid, error = validate_country_code(destination_country)
                if not is_valid:
                    raise SIPTrunkConfigurationError(
                        f"Invalid destination_country: {error}"
                    )


            from livekit.protocol import models as models_proto

            update = sip_proto.SIPOutboundTrunkUpdate()


            if name is not None:
                update.name = name
            if address is not None:
                update.address = address
            if transport is not None:
                update.transport = transport.value if isinstance(transport, SIPTransport) else transport
            if destination_country is not None:
                update.destination_country = destination_country or ""
            if auth_username is not None:
                update.auth_username = auth_username
            if auth_password is not None:
                update.auth_password = auth_password
            if metadata is not None:
                import json
                update.metadata = json.dumps(metadata)


            if numbers is not None:
                normalized_numbers = []
                if validate_numbers:
                    for num in numbers:
                        is_valid, error = validate_e164_phone_number(num)
                        if not is_valid:
                            try:
                                normalized = normalize_phone_number(num)
                                normalized_numbers.append(normalized.replace("tel:", ""))
                            except ValueError:
                                raise SIPTrunkError(
                                    f"Invalid phone number '{num}': {error}. "
                                    f"Expected E.164 format (e.g., +14155551234)"
                                )
                        else:
                            normalized_numbers.append(num.replace("tel:", ""))
                else:
                    normalized_numbers = [n.replace("tel:", "") for n in numbers]


                list_update = models_proto.ListUpdate()
                list_update.values[:] = normalized_numbers
                update.numbers.CopyFrom(list_update)

            lk_api = await self._get_api()

            request = sip_proto.UpdateSIPOutboundTrunkRequest(
                sip_trunk_id=trunk_id,
                update=update,
            )
            response = await lk_api.sip.update_sip_outbound_trunk(request)


            updated_transport = transport if transport is not None else existing_trunk.transport
            if isinstance(response.transport, str):
                updated_transport = SIPTransport(response.transport)

            updated_trunk = OutboundTrunkConfig(
                trunk_id=response.sip_trunk_id,
                name=response.name,
                address=response.address,
                numbers=list(response.numbers),
                auth_username=response.auth_username or "",
                auth_password=response.auth_password or "",
                transport=updated_transport,
                destination_country=response.destination_country if response.destination_country else None,
                metadata=metadata if metadata is not None else existing_trunk.metadata,
            )

            logger.info(f"Updated outbound trunk: {updated_trunk.trunk_id}")
            return updated_trunk

        except SIPTrunkNotFoundError:
            raise
        except SIPTrunkConfigurationError:
            raise
        except Exception as e:
            logger.error(f"Error updating outbound trunk {trunk_id}: {e}")
            raise SIPTrunkError(f"Failed to update outbound trunk: {e}")

    async def delete_outbound_trunk(self, trunk_id: str) -> None:

        try:
            lk_api = await self._get_api()
            request = sip_proto.DeleteSIPTrunkRequest(sip_trunk_id=trunk_id)
            await lk_api.sip.delete_sip_trunk(request)
            logger.info(f"Deleted outbound trunk: {trunk_id}")
        except Exception as e:
            logger.error(f"Error deleting outbound trunk {trunk_id}: {e}")
            raise SIPTrunkError(f"Failed to delete outbound trunk: {e}")





    async def get_default_outbound_trunk(self) -> Optional[OutboundTrunkConfig]:

        if self._config.default_outbound_trunk_id:
            try:
                return await self.get_outbound_trunk(
                    self._config.default_outbound_trunk_id
                )
            except SIPTrunkNotFoundError:
                logger.warning(
                    f"Default outbound trunk not found: "
                    f"{self._config.default_outbound_trunk_id}"
                )


        trunks = await self.list_outbound_trunks()
        if trunks:
            return trunks[0]
        return None

    async def find_trunk_for_number(
        self, phone_number: str, direction: str = "outbound"
    ) -> Optional[str]:

        from .config import normalize_phone_number


        try:
            normalized = normalize_phone_number(phone_number)

            cleaned_number = normalized.replace("tel:", "").replace("+", "")
        except ValueError:
            logger.warning(f"Could not normalize phone number for trunk lookup: {phone_number}")
            cleaned_number = phone_number.replace("tel:", "").replace("+", "")

        if direction == "outbound":
            trunks = await self.list_outbound_trunks()
        else:
            trunks = await self.list_inbound_trunks()

        for trunk in trunks:

            for trunk_num in trunk.numbers:
                trunk_cleaned = trunk_num.replace("tel:", "").replace("+", "")

                if trunk_cleaned == cleaned_number:
                    logger.info(f"Found exact match trunk {trunk.trunk_id} for {phone_number}")
                    return trunk.trunk_id

                if "*" in trunk_cleaned:
                    prefix = trunk_cleaned.replace("*", "")
                    if cleaned_number.startswith(prefix):
                        logger.info(f"Found prefix match trunk {trunk.trunk_id} for {phone_number}")
                        return trunk.trunk_id

        logger.warning(f"No matching trunk found for {phone_number} (direction: {direction})")
        return None
