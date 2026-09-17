#!/usr/bin/env bash
# Free to build. Publishing needs a PyPI token (still $0 cash).
set -euo pipefail
cd "$(dirname "$0")/.."
python -m pip install -U build twine
rm -rf dist build *.egg-info
python -m build
echo "Artifacts in dist/. To publish: TWINE_USERNAME=__token__ TWINE_PASSWORD=pypi-... twine upload dist/*"
