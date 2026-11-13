#!/bin/bash

cd /root/tg_sentinel || exit 1

exec > >(tee -a /root/tg_sentinel/linkedin_forwarder_output.log) 2>&1

source venv/bin/activate || { echo "Failed to activate virtual environment"; exit 1; }
echo "Activated virtual environment"

python linkedin_forwarder.py "$@"
