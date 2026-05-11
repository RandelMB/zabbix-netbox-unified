from typing import Optional

from pydantic import BaseModel


class NetBoxInterfaceSyncRunPayload(BaseModel):
    rename_interfaces: bool = True
    sync_descriptions: bool = True
    sync_mac_addresses: bool = True
    sync_enabled_state: bool = True
    sync_type: bool = True
    sync_mtu: bool = True
    sync_vlan_tags: bool = True
    sync_lag_members: bool = True
    sync_connections: bool = True
    create_missing_interfaces: bool = False
    replace_existing_interfaces: bool = True
    reassign_primary_ip: bool = True
    management_interface_name: Optional[str] = None
