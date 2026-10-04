#!/usr/bin/env sh
# Watched synthetic service entry point.
exec python3 fixtures/fake_services.py "$@"
