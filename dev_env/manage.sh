#!/bin/bash

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

ACTION=$1

case "$ACTION" in
  sync-git)
    echo "☁️  Fetching latest from GitHub..."
    git fetch --all

    echo "🏠 Updating local 'main'..."
    git checkout main
    git pull origin main

    echo "🛠️  Updating local 'dev'..."
    git checkout dev
    git pull origin dev

    echo "🔀 Merging 'main' into 'dev'..."
    if git merge main -m "chore: sync with main"; then
        echo "🚀 Pushing synced dev branch to GitHub..."
        git push origin dev
        echo "✅ Everything is up to date and in sync!"
    else
        echo "⚠️  CONFLICTS FOUND!"
        echo "Git couldn't auto-merge. Look at the red files in your sidebar."
        echo "Fix them, save, and commit to finish the sync manually."
        # We exit with an error code so the VS Code task shows a 'failed' notification
        exit 1
    fi
    ;;

  reset-dev)
    echo "☢️  Preparing to hard reset 'dev' to 'main'..."
    
    # Safety Check: Are there uncommitted changes?
    if ! git diff-index --quiet HEAD --; then
        echo "❌ ERROR: You have uncommitted changes! Commit them or stash them first."
        exit 1
    fi

    echo "☁️  Fetching latest from GitHub..."
    git fetch --all

    echo "🏠 Updating local 'main'..."
    git checkout main
    git pull origin main

    echo "🧹 Wiping 'dev' and matching it to 'main'..."
    git checkout dev
    git reset --hard main
    
    echo "🚀 Force-pushing clean 'dev' to GitHub..."
    git push origin dev --force

    echo "✨ 'dev' is now a clean mirror of 'main'. The ghosts are gone!"
    ;;
      
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