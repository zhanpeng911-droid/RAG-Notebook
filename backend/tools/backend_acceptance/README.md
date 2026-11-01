# Backend acceptance tools

Isolated load and acceptance harness for the backend. It targets generated data and
isolated Compose projects only; it does not use the normal project's `.env`,
containers, or persistent volumes. Test-only credentials are public placeholders
and must not be used elsewhere. No service exposes a host port.

The acceptance image uses `Dockerfile.backend-acceptance.dockerignore`, a source
allowlist; do not use the normal backend Dockerfile for acceptance builds. The
Ollama protocol double generates a deterministic 32-dimensional vector and builds
citations from the request; its results do not measure real model quality.

From the repository root, choose a new ID for each run:

```powershell
python backend/tools/backend_acceptance/runner.py preflight --run-id nb-it-20261027-example
python backend/tools/backend_acceptance/runner.py run --run-id nb-it-20261027-example --suite storage
python backend/tools/backend_acceptance/runner.py report --run-id nb-it-20261027-example
python backend/tools/backend_acceptance/runner.py cleanup --run-id nb-it-20261027-example
```

Preflight creates `docs/evidence/backend-tests/<run-id>` locally and never
overwrites an existing run. Exit 0 means an action completed; nonzero means
failure, incomplete preflight, or an unsupported/missing suite. The preflight
currently covers a subset (MySQL connection/migration, Redis ping, module paths,
Chroma persistence) and exits nonzero even on a successful subset because
cross-run isolation and API/worker broker identity are not yet established.
Only the subset passing permits a subsequent targeted suite, whose result is not
full acceptance.

Cleanup requires the run manifest, a matching restricted project name and matching
container/volume labels; it deletes only that project's containers and volumes.
Evidence stays on disk. If the source or tests change after an image is built,
create a new run and rebuild; a running container does not automatically receive
source changes.
