#!/usr/bin/env bash
# Runs in the empty workspace with HOME set to the run's own home.
# Applies hub there, where pr is on by default, and makes a repo on a feature branch
# whose remote is a bare repo in the workspace, so nothing can reach GitHub.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
. "$here/../../home_guard.sh"
step="$here/../../../plugins/hub/lib/hub_switch.py"
python3 "$step" apply
git init -q --bare origin.git
git init -q repo
cd repo
git config user.name Sam
git config user.email sam@example.com
git remote add origin ../origin.git
mkdir -p .github
printf 'def upload(path):\n    return send(path)\n' > upload.py
cat > CONTRIBUTING.md <<'EOF'
# Contributing

Start every PR title with the ticket id from the branch name, such as SHOP-12.
Open PRs ready for review, not as drafts. Use the PR template.
EOF
printf '## Summary\n\n## Testing\n' > .github/pull_request_template.md
git add . && git commit -q -m "Start the shop"
git push -q origin HEAD:main
git checkout -q -b feat/SHOP-12-upload-retry
printf 'def upload(path, tries=3):\n    for _ in range(tries - 1):\n        try:\n            return send(path)\n        except OSError:\n            pass\n    return send(path)\n' > upload.py
git commit -q -am "Retry the upload three times"
