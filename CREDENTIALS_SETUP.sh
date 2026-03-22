#!/bin/bash
# CREDENTIALS_SETUP.sh - Securely set up your .env file
# Run this script and it will prompt you for credentials
# All input is kept local and never echoed to the terminal

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$PROJECT_DIR/.env"

echo "=========================================="
echo "Polymarket Bot - Credentials Setup"
echo "=========================================="
echo ""
echo "This script will create a .env file with your credentials."
echo "Your credentials will ONLY be stored locally in $ENV_FILE"
echo ".env is in .gitignore and will never be committed to git."
echo ""

# Check if .env already exists
if [ -f "$ENV_FILE" ]; then
    read -p ".env already exists. Overwrite? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 1
    fi
fi

# Start creating the .env file
> "$ENV_FILE"
chmod 600 "$ENV_FILE"  # Restrict to owner read/write only

echo "Polymarket Credentials (get from https://polymarket.com)"
echo "=========================================="
echo ""

read -p "POLYMARKET_API_KEY: " -r PM_KEY
read -p "POLYMARKET_SECRET: " -r PM_SECRET
read -p "POLYMARKET_PASSPHRASE: " -r PM_PASSPHRASE
read -p "POLYMARKET_PRIVATE_KEY (hex string): " -r PM_PRIVKEY

echo ""
echo "Kalshi Credentials (get from https://kalshi.com)"
echo "=========================================="
echo ""

read -p "KALSHI_API_KEY_ID: " -r KALSHI_KEY_ID
read -p "KALSHI_PRIVATE_KEY_PATH (e.g., ./kalshi-private-key.pem): " -r KALSHI_KEY_PATH

echo ""
echo "Kalshi Environment"
echo "=========================================="
echo "Choose your environment:"
echo "  1) Demo (https://demo-api.kalshi.co)"
echo "  2) Production (https://trading-api.kalshi.com)"
read -p "Choice (1 or 2)? " -r ENV_CHOICE

if [ "$ENV_CHOICE" = "2" ]; then
    KALSHI_URL="https://trading-api.kalshi.com"
else
    KALSHI_URL="https://demo-api.kalshi.co"
fi

# Write .env file
cat > "$ENV_FILE" << EOF
# Polymarket Credentials
POLYMARKET_API_KEY=$PM_KEY
POLYMARKET_SECRET=$PM_SECRET
POLYMARKET_PASSPHRASE=$PM_PASSPHRASE
POLYMARKET_PRIVATE_KEY=$PM_PRIVKEY

# Kalshi Credentials
KALSHI_API_KEY_ID=$KALSHI_KEY_ID
KALSHI_PRIVATE_KEY_PATH=$KALSHI_KEY_PATH
KALSHI_BASE_URL=$KALSHI_URL

# Optional
LOG_DIR=./logs
TRADING_MODE=OBSERVE
EOF

echo ""
echo "=========================================="
echo "✅ Credentials saved to: $ENV_FILE"
echo "=========================================="
echo ""
echo "File permissions: $(ls -l "$ENV_FILE" | awk '{print $1}')"
echo ""
echo "Next steps:"
echo "1. Verify Kalshi private key exists:"
echo "   ls -la $(dirname "$KALSHI_KEY_PATH")/"
echo ""
echo "2. Test the connection:"
echo "   python3 scripts/run_core_mm.py --exchange kalshi --mode OBSERVE --runtime-root tmp/test --duration-secs 60"
echo ""
echo "3. For Polymarket, test similarly:"
echo "   python3 scripts/run_core_mm.py --exchange polymarket --mode OBSERVE --runtime-root tmp/test --duration-secs 60"
echo ""
