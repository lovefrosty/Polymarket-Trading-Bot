# Terminal Commands for Secure Credential Setup

## Option 1: Interactive Setup Script (Recommended)

The safest way - credentials are never echoed to your terminal:

```bash
cd ~/Desktop/Polymarket\ Bot
chmod +x CREDENTIALS_SETUP.sh
./CREDENTIALS_SETUP.sh
```

This will:
1. Prompt you for each credential (input is hidden)
2. Create `.env` with `600` permissions (read/write owner only)
3. Show verification steps

---

## Option 2: Manual Setup (If Script Fails)

### Step 1: Create .env file with restricted permissions

```bash
cd ~/Desktop/Polymarket\ Bot

# Create empty .env with secure permissions (owner read/write only)
touch .env
chmod 600 .env

# Verify permissions are correct (should show: -rw-------)
ls -la .env
```

### Step 2: Add your credentials

Use a text editor to edit `.env`:

```bash
nano .env
```

**Or** use `cat` with a heredoc (credentials stay in your terminal history, so CLEAR IT after):

```bash
cat >> .env << 'EOF'
# Polymarket Credentials
POLYMARKET_API_KEY=your_polymarket_api_key
POLYMARKET_SECRET=your_polymarket_secret
POLYMARKET_PASSPHRASE=your_polymarket_passphrase
POLYMARKET_PRIVATE_KEY=your_polymarket_private_key_hex

# Kalshi Credentials
KALSHI_API_KEY_ID=your_kalshi_api_key_id
KALSHI_PRIVATE_KEY_PATH=./kalshi-private-key.pem
KALSHI_BASE_URL=https://demo-api.kalshi.co

# Optional
LOG_DIR=./logs
TRADING_MODE=OBSERVE
EOF
```

**Then clear your bash history to remove credentials from memory:**

```bash
# Clear current session history
history -c

# Clear history file
cat /dev/null > ~/.bash_history

# For zsh (if you use it)
cat /dev/null > ~/.zsh_history
```

---

## Option 3: Environment Variables Only (No File)

If you prefer NOT to write credentials to disk, set them as environment variables:

```bash
export POLYMARKET_API_KEY="your_key"
export POLYMARKET_SECRET="your_secret"
export POLYMARKET_PASSPHRASE="your_passphrase"
export POLYMARKET_PRIVATE_KEY="your_key_hex"
export KALSHI_API_KEY_ID="your_kalshi_key_id"
export KALSHI_PRIVATE_KEY_PATH="./kalshi-private-key.pem"
export KALSHI_BASE_URL="https://demo-api.kalshi.co"

# Then run the bot
python3 scripts/run_core_mm.py --exchange kalshi --mode OBSERVE --runtime-root tmp/test --duration-secs 60
```

**Downsides:**
- You have to set vars every time you open a new terminal
- Credentials are in your shell history
- Doesn't persist across terminal sessions

---

## Option 4: Use 1Password / macOS Keychain (Most Secure)

Store credentials in your system keychain, then load them:

### A. Store in Keychain

```bash
# Store Polymarket credentials
security add-generic-password -a "polymarket_api_key" -s "Polymarket API" -w "your_api_key"
security add-generic-password -a "polymarket_secret" -s "Polymarket Secret" -w "your_secret"
security add-generic-password -a "polymarket_passphrase" -s "Polymarket Passphrase" -w "your_passphrase"
security add-generic-password -a "polymarket_private_key" -s "Polymarket PrivKey" -w "your_key_hex"

# Store Kalshi credentials
security add-generic-password -a "kalshi_api_key_id" -s "Kalshi API Key ID" -w "your_kalshi_key_id"
```

### B. Create a load script

```bash
cat > ~/Desktop/Polymarket\ Bot/load_from_keychain.sh << 'EOF'
#!/bin/bash
export POLYMARKET_API_KEY=$(security find-generic-password -a "polymarket_api_key" -s "Polymarket API" -w)
export POLYMARKET_SECRET=$(security find-generic-password -a "polymarket_secret" -s "Polymarket Secret" -w)
export POLYMARKET_PASSPHRASE=$(security find-generic-password -a "polymarket_passphrase" -s "Polymarket Passphrase" -w)
export POLYMARKET_PRIVATE_KEY=$(security find-generic-password -a "polymarket_private_key" -s "Polymarket PrivKey" -w)
export KALSHI_API_KEY_ID=$(security find-generic-password -a "kalshi_api_key_id" -s "Kalshi API Key ID" -w)
export KALSHI_PRIVATE_KEY_PATH="./kalshi-private-key.pem"
export KALSHI_BASE_URL="https://demo-api.kalshi.co"
EOF

chmod +x ~/Desktop/Polymarket\ Bot/load_from_keychain.sh
```

### C. Load and run

```bash
source ~/Desktop/Polymarket\ Bot/load_from_keychain.sh
python3 scripts/run_core_mm.py --exchange kalshi --mode OBSERVE --runtime-root tmp/test --duration-secs 60
```

---

## Kalshi Private Key Setup

Before setting credentials, generate your RSA keypair:

```bash
cd ~/Desktop/Polymarket\ Bot

# Generate 4096-bit RSA private key
openssl genrsa -out kalshi-private-key.pem 4096

# Extract public key
openssl rsa -in kalshi-private-key.pem -pubout -out kalshi-public-key.pem

# View public key (upload this to Kalshi dashboard)
cat kalshi-public-key.pem

# Verify private key is readable
ls -la kalshi-private-key.pem
```

**Upload the content of `kalshi-public-key.pem` to Kalshi Settings → API Keys**

Then set `KALSHI_PRIVATE_KEY_PATH=./kalshi-private-key.pem` in your credentials.

---

## Verify Your Setup

After credentials are set, test them:

### Check .env file exists
```bash
cat ~/.env | head -5  # Shows first 5 lines without revealing values
```

### Test Polymarket connection
```bash
python3 scripts/run_core_mm.py \
  --exchange polymarket \
  --mode OBSERVE \
  --runtime-root tmp/pm-test \
  --duration-secs 60 \
  --symbol BTC
```

**Success indicators:**
- No "API key" errors in output
- Status JSON shows markets found
- `applied_book_updates > 0`

### Test Kalshi connection
```bash
python3 scripts/run_core_mm.py \
  --exchange kalshi \
  --mode OBSERVE \
  --runtime-root tmp/kalshi-test \
  --duration-secs 60 \
  --symbol BTC
```

**Success indicators:**
- No "RSA signature" errors
- Status JSON shows Kalshi markets found
- `applied_book_updates > 0`

---

## Security Summary

| Method | Security | Convenience | Persistence |
|--------|----------|-------------|-------------|
| Script (`CREDENTIALS_SETUP.sh`) | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ✅ (one-time) |
| Manual `.env` + history clear | ⭐⭐⭐⭐ | ⭐⭐⭐ | ✅ (one-time) |
| Environment variables | ⭐⭐⭐ | ⭐⭐ | ❌ (per session) |
| macOS Keychain | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ✅ (system-level) |

**Recommended:** Use `CREDENTIALS_SETUP.sh` (Option 1) — it's the easiest and safest.

---

## IMPORTANT: Cleanup

After you're done, **never commit `.env`** (it's in `.gitignore`):

```bash
# Verify .env is NOT staged
git status

# Should show: nothing to commit (only .env untracked)
```

If you ever accidentally commit credentials:

```bash
# Remove from git history (nuclear option)
git filter-branch --tree-filter 'rm -f .env' HEAD

# Better: use git-secret or similar tools for shared repos
```

---

## Troubleshooting

### "Permission denied" on .env
```bash
chmod 600 ~/.env
```

### "API key not found"
```bash
# Check .env exists and is readable
cat .env | grep POLYMARKET_API_KEY
cat .env | grep KALSHI_API_KEY_ID
```

### "RSA signature error" (Kalshi)
```bash
# Verify private key path is correct and readable
ls -la ./kalshi-private-key.pem

# If missing, regenerate:
openssl genrsa -out kalshi-private-key.pem 4096
```

### "Private key file not found"
```bash
# Make sure you're in the project directory
cd ~/Desktop/Polymarket\ Bot
ls kalshi-private-key.pem
```

---

Done! Start with **Option 1** (the script) — it's the safest approach.
