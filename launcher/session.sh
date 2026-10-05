#!/bin/bash
# X session for the GPi console. Started by gpi-console.service via xinit.
# Start PulseAudio for Bluetooth audio (AirPods)
pulseaudio --start 2>/dev/null
xset -dpms
xset s off
xset s noblank
exec python3 /opt/gpi/launcher/launcher.py
