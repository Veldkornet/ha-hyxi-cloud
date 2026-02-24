#!/bin/bash

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

ACTION=$1

case "$ACTION" in
  start)
    echo "🧹 Wiping old Sandbox..."
    rm -rf ha_testing_config
    mkdir -p ha_testing_config

    if [ -d "ha_testing_seed" ]; then
        echo "🌱 Seeding from Golden Image..."
        cp -Rp ha_testing_seed/. ha_testing_config/
    else
        echo "⚠️ No ha_testing_seed found! Starting a fresh instance (Onboarding required)."
    fi

    echo "🚀 Starting Home Assistant..."
    docker compose up -d
    echo "✅ Ready at http://localhost:8123"
    ;;

  stop)
    echo "🛑 Stopping Home Assistant..."
    docker compose down
    echo "✨ Stopped."
    ;;

  restart)
    echo "🔄 Restarting Sandbox..."
    $0 stop
    $0 start
    ;;

  *)
    echo "Usage: $0 {start|stop|restart}"
    exit 1
    ;;
esac