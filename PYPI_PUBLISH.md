# PyPI publish checklist — slimeflow 0.2.1

Free-lane release checklist; publish only the verified artifacts below.

## Preflight

- [ ] Confirm package/version is `slimeflow 0.2.1`.
- [ ] From `/workspace/slime-flow/python-sdk`, activate the `.venv-build` virtualenv. (System pip is PEP 668 blocked.)
- [ ] Confirm `PYPI_API_TOKEN` is present without printing it:

  ```bash
  test -n "${PYPI_API_TOKEN:-}" || { echo "PYPI_API_TOKEN is not set" >&2; exit 1; }
  ```

- [ ] Run the check and confirm it passes:

  ```bash
  python -m twine check dist/slimeflow-0.2.1*
  ```

- [ ] Confirm the upload files and hashes:

  | Artifact | Size | SHA-256 |
  | --- | ---: | --- |
  | `dist/slimeflow-0.2.1-py3-none-any.whl` | 18,260 bytes | `2d61ab986774a57ef986addc07cc719e5c6d254f6a4ad5d1dac2d11f566f317b` |
  | `dist/slimeflow-0.2.1.tar.gz` | 19,874 bytes | `e84408a16e86bf6fb2633073202a62e4471f579c579f9bedcb65fbc33a84bdc9` |

## Upload

Run exactly from `python-sdk` with `.venv-build` active:

```bash
cd /workspace/slime-flow/python-sdk
source .venv-build/bin/activate
export TWINE_USERNAME=__token__
export TWINE_PASSWORD="$PYPI_API_TOKEN"
twine upload dist/slimeflow-0.2.1*
```

## Post-publish verification

- [ ] Open https://pypi.org/project/slimeflow/0.2.1/ and confirm the release and both artifacts are listed.
- [ ] In a clean environment, run:

  ```bash
  python -m pip install slimeflow==0.2.1
  ```

- [ ] Smoke-test the installed package and record any issue before outreach.

## README and badge note

If the package URL should be added, update the README after PyPI is live with a link to https://pypi.org/project/slimeflow/ (and a PyPI version badge if desired). Keep that documentation change separate from the upload.

## Rollback

Do not yank casually. Yank `0.2.1` only for a critical release problem, after confirming the impact and preparing a corrected release; document the reason and follow-up version.
