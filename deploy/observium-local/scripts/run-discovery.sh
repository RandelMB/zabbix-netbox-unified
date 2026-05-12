#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <device_id|hostname-wildcard> [modules]" >&2
  exit 1
fi

device_ref="$1"
modules="${2:-os,ports,ports-stack,vlans,inventory,neighbours,ip-addresses,arp-table,mibs}"

exec docker exec observium-app php /opt/observium/discovery.php -h "$device_ref" -m "$modules"

