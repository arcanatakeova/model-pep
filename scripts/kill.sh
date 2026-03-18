#!/bin/bash
# Emergency kill switch — creates STOP file that halts all agent activity within 60 seconds
touch "$(dirname "$0")/../STOP"
echo "KILL SWITCH ACTIVATED — All agent activity will halt within 60 seconds."
echo "To resume: rm STOP"
