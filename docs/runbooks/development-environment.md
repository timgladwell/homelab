# Setting Up a Development Environment

Everything needed to change this repo and validate the change locally. Nothing
here touches a cluster.

## Pre-checks

- A clone of the repo, and `gh` authenticated.
- The age private key for each site whose secrets you will edit. Secrets are
  encrypted per site (`.sops.yaml`), so you only need the keys for the sites you
  touch.

## Steps

1. **Install the validation tools** at the versions pinned in
   `.github/workflows/validate.yml`, which is what CI runs: `flux`, `kustomize`,
   `kubeconform`, `kube-score`, `trivy`, `conftest`, `yamllint`, `alloy`,
   `flate` and `python3`, plus `sops` and `age`.
   - The `flux` pin tracks what the clusters run, not the newest release; see
     [Flux Upgrades](flux-upgrades.md).
   - `brew install grafana/grafana/alloy` — plain `brew install alloy` is an
     unrelated package.

2. **Install the git hooks**, which refuse a commit or push containing an
   unencrypted secret:

   ```bash
   ./scripts/setup-git-hooks.sh
   ```

   CI repeats the check (validation step 10), since hooks can be skipped.

3. **Point `sops` at your keys.** `SOPS_AGE_KEY_FILE` defaults to
   `~/.config/sops/age/keys.txt`; put each site key you hold there. Edit secrets
   only through the helper, which decrypts, opens an editor and re-encrypts:

   ```bash
   ./scripts/secrets-helper.sh edit sites/<site>/<layer>/<name>-secret.sops.yaml
   ./scripts/secrets-helper.sh view <file>
   ./scripts/secrets-helper.sh encrypt <file>     # a new plaintext file, in place
   ```

## Working on a change

**Validate everything** — the same sixteen steps CI runs, per site:

```bash
./scripts/validate-k3s.sh
```

**Build one layer or component** while iterating. Plain `kustomize build` does
not substitute `${VAR}` placeholders; the full pipeline does.

```bash
kustomize build sites/akron/infrastructure/
kustomize build base/dns/
```

**Read rendered charts.** Every other step stops at the `HelmRelease`, so this
is the only place Prometheus, Grafana, Loki and the Alloys are visible as
workloads:

```bash
flate build hr -p clusters/akron                      # every chart-rendered object
flate diff all -p clusters/akron --base origin/main   # what this branch changes
```

Every PR also gets the `flate diff` as a comment. Use `flate`, not `flux-local`,
which is deprecated and segfaults on current Flux.

If `flate diff` fails with
`core.repositoryformatversion does not support extension: worktreeconfig`,
something has set git's `extensions.worktreeConfig`, which go-git cannot open;
`git config --unset extensions.worktreeConfig` clears it. `flate build` is
unaffected.

**Check an Alloy config** while editing it. `${VAR}` placeholders make the
local check fail; validation step 12 checks the hydrated config properly.

```bash
alloy fmt base/metrics-collection/alloy-metrics.alloy
alloy validate base/metrics-collection/alloy-metrics.alloy
```

**Run the Python tests** for `base/landing/app.py` and `base/pihole-sync/sync.py`:

```bash
python3 -m unittest discover -s tests
```

## Post-checks

`./scripts/validate-k3s.sh` ends with every step `PASS`, and `git status` shows
no `*secret*.yaml` without `sops:` metadata.
