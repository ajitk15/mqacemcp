#!/usr/bin/env bash
# =============================================================================
# CONSOLIDATED ACE SETUP — both integration nodes on ONE host
#
# Creates NODE1 and NODE2 with IDENTICAL configuration by reusing `setup`,
# associating each with the queue manager created by the MQ setup
# (NODE1 -> MQNODE1, NODE2 -> MQNODE2). Run the MQ setup first so those QMs exist.
#
# Run (Linux, after sourcing the ACE environment):
#   . /opt/ibm/ace-12/server/bin/mqsiprofile
#   bash all_nodes_full_setup.sh
# =============================================================================

cd "$(dirname "$0")"

echo "=== NODE1 (QM MQNODE1) ==="
bash setup NODE1 MQNODE1

echo "=== NODE2 (QM MQNODE2) ==="
bash setup NODE2 MQNODE2

echo "Done."
