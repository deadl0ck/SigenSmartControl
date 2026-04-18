#!/bin/bash
cd /home/martin/git/SigenSmartControl
source .venv/bin/activate
nohup python main.py > monitor.log 2>&1 &
echo "Monitor started. PID: $!"
echo "View logs with: tail -f monitor.log"
