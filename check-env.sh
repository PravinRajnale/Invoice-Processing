#!/usr/bin/env bash
# Fails if a real key has been pasted into the committed template.
cd "$(dirname "$0")"
if [ -f .env.example ] && grep -qE '^AZURE_OPENAI_API_KEY=.+' .env.example; then
  echo "REFUSING TO START: a key is present in .env.example, which is committed."
  echo "Move it to .env (ignored by git) and blank the line in .env.example."
  exit 1
fi
