#!/usr/bin/env bash
# install_gcloud.sh — Install the Google Cloud CLI (includes gsutil) on Ubuntu/Debian.
# After installation, run: gcloud auth login
# Then use: gsutil cp, gsutil ls, gsutil rsync, etc.

set -euo pipefail

echo "==> Installing Google Cloud CLI..."

# Add Google's apt signing key
curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
    | sudo gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg

# Add the Cloud SDK repo
echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
    | sudo tee /etc/apt/sources.list.d/google-cloud-sdk.list > /dev/null

# Install
sudo apt-get update -q
sudo apt-get install -y google-cloud-cli

echo ""
echo "==> Done. Installed: $(gcloud --version | head -1)"
echo ""
echo "Next steps:"
echo "  1. Authenticate:       gcloud auth login"
echo "  2. Set your project:   gcloud config set project YOUR_PROJECT_ID"
echo "  3. Upload:             gsutil cp local_file.txt gs://your-bucket/"
echo "  4. Download:           gsutil cp gs://your-bucket/file.txt ."
echo "  5. Sync a directory:   gsutil -m rsync -r ./local-dir gs://your-bucket/dir"
