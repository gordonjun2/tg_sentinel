#!/bin/bash

exec > >(tee -a /root/tg_sentinel/luma_reminder_output.log) 2>&1

source venv/bin/activate || { echo "Failed to activate virtual environment"; exit 1; }
echo "Activated virtual environment"

python luma_reminder.py
