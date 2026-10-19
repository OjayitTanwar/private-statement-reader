#!/bin/zsh
set -e
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
echo "Private Statement Reader — free local engine setup"
if ! command -v brew >/dev/null; then
  echo "Install Homebrew from https://brew.sh, then run this setup again."
  read -k 1 "?Press any key to close."
  exit 1
fi
brew install python python@3.13 tesseract ocrmypdf poppler
echo "Setup complete. Open Private Statement Reader.app."
read -k 1 "?Press any key to close."
