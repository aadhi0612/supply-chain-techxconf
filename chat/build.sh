#!/usr/bin/env bash
# Build the Lambda zip: handler + the dependency-free siliconflow.trace package. boto3 comes with the Python runtime.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf chat/build && mkdir -p chat/build/pkg/siliconflow
cp chat/handler.py chat/build/pkg/
cp siliconflow/__init__.py chat/build/pkg/siliconflow/
cp -r siliconflow/trace chat/build/pkg/siliconflow/trace
find chat/build/pkg -name __pycache__ -prune -exec rm -rf {} +
python3 - <<'PY'
import os, zipfile
root = "chat/build/pkg"
with zipfile.ZipFile("chat/build/lambda.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for d, _, fs in sorted(os.walk(root)):
        for f in sorted(fs):
            p = os.path.join(d, f)
            zi = zipfile.ZipInfo(os.path.relpath(p, root), (2026, 1, 1, 0, 0, 0))   # fixed mtime: reproducible hash
            zi.external_attr = 0o644 << 16
            z.writestr(zi, open(p, "rb").read(), zipfile.ZIP_DEFLATED)
PY
echo "built chat/build/lambda.zip ($(stat -c %s chat/build/lambda.zip) bytes)"
