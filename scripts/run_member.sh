#!/usr/bin/env bash
# Usage: MEMBER_NAME=Alex MEMBER_PORT=8001 ./scripts/run_member.sh
# Seed: MEMBER_AGENT_SEED, else MEMBER_SEED_<NAME> (as used by print_addresses.py), else a dev seed.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${MEMBER_NAME:?set MEMBER_NAME}"
: "${MEMBER_PORT:?set MEMBER_PORT}"
upper=$(echo "$MEMBER_NAME" | tr '[:lower:]' '[:upper:]')
seed_var="MEMBER_SEED_${upper}"
export MEMBER_AGENT_SEED="${MEMBER_AGENT_SEED:-${!seed_var:-}}"
exec "${PYTHON:-.venv/bin/python}" -m member_agent.agent
