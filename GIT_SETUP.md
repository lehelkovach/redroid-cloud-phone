# Git Repository Setup Guide

## Do You Need a GitHub Repo?

### ✅ **YES - Recommended!**

**Benefits of Remote GitHub Repo:**
1. **Backup** - Protect your work from local machine failures
2. **Handoff** - Easy to share with other agents/developers
3. **Version Control** - Track changes and rollback if needed
4. **Collaboration** - Multiple people can work on it
5. **CI/CD** - Can set up automated testing/deployment
6. **Documentation** - GitHub provides nice markdown rendering

### ⚠️ **Security Considerations**

**What to Include:**
- ✅ All scripts and code
- ✅ Documentation (`.md` files)
- ✅ Configuration templates (without secrets)
- ✅ Test scripts

**What NOT to Include:**
- ❌ SSH private keys (`~/.ssh/waydroid_oci`)
- ❌ OCI API keys (`~/.oci/oci_api_key.pem`)
- ❌ OCI config with credentials (`~/.oci/config`)
- ❌ Instance IPs (if sensitive)
- ❌ Passwords or secrets

**Solution:** Use `.gitignore` to exclude sensitive files

---

## Setup Options

### Option 1: Public GitHub Repo (Recommended for Open Source)

**Pros:**
- Free
- Easy sharing
- Community contributions
- Public documentation

**Cons:**
- Publicly visible
- Don't commit secrets

**Best For:**
- Open source projects
- Learning/sharing
- Public documentation

### Option 2: Private GitHub Repo (Recommended for Production)

**Pros:**
- Private and secure
- Still easy to share with team
- Free for personal use

**Cons:**
- Requires GitHub account
- Limited free private repos (but usually enough)

**Best For:**
- Production projects
- Sensitive configurations
- Team collaboration

### Option 3: Local Git Only (Not Recommended)

**Pros:**
- No external dependencies
- Complete privacy

**Cons:**
- No backup
- Hard to share
- No version history if local machine fails

**Best For:**
- Temporary experiments only

---

## Recommended Setup: Private GitHub Repo

### Step 1: Create `.gitignore`

```bash
# Create .gitignore file
cat > .gitignore << 'EOF'
# SSH Keys
*.pem
*.key
id_rsa*
waydroid_oci*

# OCI Credentials
.oci/
oci_api_key.pem
oci_config

# Sensitive Config
*secrets*
*credentials*
*.env

# Instance-specific
instance_ip.txt
*.log

# OS Files
.DS_Store
Thumbs.db
*.swp
*.swo
*~

# Python
__pycache__/
*.py[cod]
*.pyc
venv/
env/

# Node
node_modules/
npm-debug.log

# IDE
.vscode/
.idea/
*.iml

# Temporary
tmp/
temp/
*.tmp
EOF
```

### Step 2: Initialize Git (if not already)

```bash
# Initialize git repo
git init

# Add all files
git add .

# Create initial commit
git commit -m "Initial commit: Redroid cloud phone setup"
```

### Step 3: Create GitHub Repo

**Via GitHub Website:**
1. Go to https://github.com/new
2. Repository name: `redroid-cloud-phone` (recommended) or `waydroid-cloud-phone` (legacy)
3. Description: "Android cloud phone on Oracle Cloud ARM using Redroid"
4. **Visibility: Private** (recommended)
5. Don't initialize with README (we already have files)
6. Click "Create repository"

**Via GitHub CLI (if installed):**
```bash
gh repo create waydroid-cloud-phone --private --source=. --remote=origin --push
```

### Step 4: Connect and Push

```bash
# Add remote (replace USERNAME with your GitHub username)
git remote add origin https://github.com/USERNAME/waydroid-cloud-phone.git

# Or use SSH (if you have SSH keys set up)
git remote add origin git@github.com:USERNAME/waydroid-cloud-phone.git

# Push to GitHub
git branch -M main
git push -u origin main
```

---

## Alternative: GitHub Gist (For Quick Sharing)

If you just want to share specific files:

```bash
# Install gh CLI
sudo apt install gh

# Login
gh auth login

# Create gist with handoff docs
gh gist create HANDOFF.md QUICK_START.md --public
```

---

## What Should Be in the Repo

### ✅ Include These:

```
waydroid-cloud-phone/
├── scripts/              # All scripts
│   ├── test-*.sh
│   ├── fix-*.sh
│   └── ...
├── *.md                  # All documentation
│   ├── HANDOFF.md
│   ├── TEST_RESULTS_*.md
│   └── ...
├── .gitignore           # Exclude secrets
├── README.md            # Project overview
└── LICENSE              # If open source
```

### ❌ Exclude These (via .gitignore):

```
# Credentials
~/.ssh/waydroid_oci
~/.oci/oci_api_key.pem
~/.oci/config

# Instance-specific
instance_ip.txt
*.log

# Temporary files
tmp/
*.tmp
```

---

## Recommended Structure

### For Handoff to Another Agent:

**Create a `README.md` in the repo:**

```markdown
# Waydroid Cloud Phone

Android cloud phone deployment on Oracle Cloud ARM using Redroid.

## Quick Start

See `QUICK_START.md` for immediate setup.

## Full Documentation

- `HANDOFF.md` - Complete handoff guide
- `TEST_RESULTS_FULL_COVERAGE.md` - Test results
- `DEVELOPMENT_WORKFLOW.md` - Development guide

## Setup

1. Clone this repo
2. Set up OCI credentials (see `HANDOFF.md`)
3. Configure SSH key
4. Run: `./scripts/test-redroid-full.sh <INSTANCE_IP>`

## Status

✅ Operational - Redroid running on Oracle Cloud ARM
⚠️ Virtual devices pending kernel 6.8 compatibility
```

---

## Security Best Practices

### 1. Never Commit Secrets

**Use `.gitignore`:**
```gitignore
# SSH Keys
*.pem
*.key
~/.ssh/waydroid_oci

# OCI Credentials
.oci/
```

### 2. Use Environment Variables

**Create `config.example`:**
```bash
# Copy to config.local (not in git)
INSTANCE_IP=your_instance_ip
SSH_KEY_PATH=~/.ssh/waydroid_oci
```

### 3. Use GitHub Secrets (for CI/CD)

If setting up GitHub Actions:
- Store secrets in GitHub Secrets
- Never hardcode in scripts

### 4. Review Before Committing

```bash
# Check what you're about to commit
git status
git diff

# Make sure no secrets
git diff | grep -i "password\|key\|secret\|pem"
```

---

## Quick Setup Script

```bash
#!/bin/bash
# setup-git-repo.sh

set -euo pipefail

echo "=== Setting up Git Repository ==="

# Check if git is installed
if ! command -v git &> /dev/null; then
    echo "Installing git..."
    sudo apt-get update && sudo apt-get install -y git
fi

# Initialize git if not already
if [ ! -d .git ]; then
    echo "Initializing git repository..."
    git init
fi

# Create .gitignore if not exists
if [ ! -f .gitignore ]; then
    echo "Creating .gitignore..."
    cat > .gitignore << 'EOF'
# SSH Keys
*.pem
*.key
waydroid_oci*

# OCI Credentials
.oci/
oci_api_key.pem

# Sensitive
*secrets*
*credentials*
*.env
instance_ip.txt
*.log

# OS
.DS_Store
*.swp
*~
EOF
fi

# Add files
echo "Adding files..."
git add .

# Create initial commit
echo "Creating initial commit..."
git commit -m "Initial commit: Redroid cloud phone setup

- Redroid container operational
- Test suites created
- Documentation complete
- Ready for development"

echo ""
echo "✅ Git repository initialized!"
echo ""
echo "Next steps:"
echo "1. Create GitHub repo: https://github.com/new"
echo "2. Add remote: git remote add origin <URL>"
echo "3. Push: git push -u origin main"
echo ""
echo "Or use GitHub CLI:"
echo "  gh repo create waydroid-cloud-phone --private --source=. --remote=origin --push"
```

---

## Summary

### ✅ **YES - Create a Private GitHub Repo**

**Why:**
- Backup your work
- Easy handoff to other agents
- Version control
- Professional setup

**Steps:**
1. Create `.gitignore` (exclude secrets)
2. Initialize git: `git init`
3. Create GitHub repo (private)
4. Push: `git push -u origin main`

**Security:**
- Never commit SSH keys
- Never commit OCI credentials
- Use `.gitignore` properly
- Review before committing

---

## Current Status Check

Run this to see if git is set up:

```bash
# Check git status
git status

# Check if remote exists
git remote -v

# Check .gitignore
cat .gitignore
```

