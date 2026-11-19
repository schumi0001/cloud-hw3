#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

export HW3_FRONTEND_API_KEY
HW3_FRONTEND_API_KEY=$(aws apigateway get-api-key \
  --api-key kiqtnptyyh --include-value --region us-east-1 \
  --query value --output text)

python3 - <<'PY'
import json
import os
from pathlib import Path
key = os.environ.get('HW3_FRONTEND_API_KEY', '').strip()
if not key or key == 'None':
    raise SystemExit('Could not retrieve the API key; deployment stopped.')
Path('public/config.js').write_text(
    'window.PHOTO_ALBUM_CONFIG = ' + json.dumps({'apiKey': key}) + ';\n'
)
print('Configured API key without displaying its value.')
PY
unset HW3_FRONTEND_API_KEY

aws s3 sync public/ s3://hw3-frontend-fg726/ \
  --exclude config.example.js \
  --cache-control 'no-cache' \
  --region us-east-1
printf '\nWebsite: http://hw3-frontend-fg726.s3-website-us-east-1.amazonaws.com\n'
