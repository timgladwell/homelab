# Read-Only Grafana Access for Claude Code

Gives Claude Code the ability to run PromQL and LogQL queries against Akron
directly, instead of the propose-query / paste-result round trip that made
debugging #210, #216 and #219 slow. Closes #220.

Grafana ships an MCP server ([`grafana/mcp-grafana`][mcp]), and Claude Code
speaks MCP natively, so there is nothing to build or deploy — it is a binary on
the dev machine plus a Viewer-scoped token.

[mcp]: https://grafana.com/docs/grafana/latest/developer-resources/mcp/

## What this is not

It does not put the dev machine on the cluster. There is still no `kubectl`, no
`flux`, no SSH. Query access reaches exactly what Grafana's datasources reach —
Prometheus and Loki, read-only — and that is the whole of the widening. See the
boundary in `CLAUDE.md`.

## Why nothing is deployed for this

The server runs on the dev machine and talks to Grafana over the network Grafana
is already published on. Nothing in the cluster consumes the token, so the token
is not cluster state and does not belong in git — see *Recording it* below.
An MCP server running on Akron (#220's Option D) would spend memory on the
constrained node to achieve the same thing.

## 1. Create the service account

Grafana service accounts are not provisioned declaratively here, so this is a
UI step and is the reason this runbook exists.

1. <https://grafana.akron.internal.zerpzorp.com> → **Administration → Users and
   access → Service accounts → Add service account**
2. Name `claude-code`, role **Viewer**
3. **Add service account token**, no expiry set unless you want to rotate on a
   schedule
4. Copy the token — Grafana shows it once

**Viewer, not Editor.** The role is the real control; `--disable-write` below is
the second layer, not the first.

## 2. Store the token

In 1Password, as the `credential` field of an item named **Grafana MCP** in the
**homelab** vault — the reference the launcher reads is
`op://homelab/Grafana MCP/credential`.

Nowhere else. Not a dotfile, not `~/.claude.json`, not an exported variable:
the launcher below reads it from 1Password each time the server starts, so the
token never sits on disk in plaintext. An earlier version of this runbook had
it in `~/.config/homelab/grafana-token`; if that file still exists, it is a
stale copy and can be deleted once 1Password holds the token.

## 3. Install and register the server

```bash
brew install mcp-grafana
brew install --cask 1password-cli    # `op`, with the desktop app integration on
```

The server is started by a launcher script, `claude/mcp-grafana.sh` in the
[dotfiles repo](https://github.com/timgladwell/dotfiles), symlinked to
`~/.claude/mcp-grafana.sh`. It reads the token with `op read`, exports it with
`GRAFANA_URL`, and `exec`s `mcp-grafana -disable-write`. Register it:

```bash
claude mcp add-json grafana --scope user \
  '{"type":"stdio","command":"/Users/tim/.claude/mcp-grafana.sh","args":[],"env":{}}'
```

The registration holds only a path, so Claude can run it and nothing secret
lands in `~/.claude.json`. Registering the token directly in the server's
`env` would bake the plaintext into `~/.claude.json`, which is what the launcher
exists to avoid.

`-disable-write` drops every mutating tool from the server's advertised list, so
a write is not something Claude can attempt and have refused — it is not offered
at all. The Viewer role is still the first control; this is the second. It is
set in the launcher, so check it is still there after editing the script.

**`--scope user` is deliberate, and the two alternatives are both wrong here.**

`--scope local` is the default, and it is scoped to *the directory you ran it
in* — not to the machine. Registering from `~` and then working in the repo
leaves a server this session cannot see, with no error to explain why. Check
with `claude mcp list`; if it is missing, this is why. The credential is a
property of the laptop rather than of one checkout, so user scope is the honest
match.

`--scope project` writes `.mcp.json` into the repo. With the launcher it would
hold only a path, but it would also be a path on one machine, and `.mcp.json`
stays gitignored as a backstop against a token ever landing there inline:
nothing else would catch it, since the pre-commit hook and validation step 10
only inspect `*secret*.yaml`.

To move one that landed in the wrong scope:

```bash
claude mcp remove grafana        # from the directory it was registered in
```

**Starting the server costs one 1Password approval.** A cold `op` session raises
a touch-to-approve prompt in the desktop app and blocks until it is answered.
That is once per Claude Code session, not per query.

## 4. Verify

**MCP servers load at startup, so restart Claude Code first** — a server
registered mid-session does not appear until then.

```bash
claude mcp list          # grafana should report ✔ Connected
```

`✔ Connected` alone proves a fair amount: 1Password released the token, the
name resolved through PiHole, Traefik routed it, the wildcard certificate
validated, and the token authenticated. A failure is one of those five, in that
order.

**A bare "connection timed out after 30000ms" is almost always the first one**,
not Grafana. Claude Code gives an MCP server 30 seconds to start; the launcher
gives the 1Password prompt 25 of them and then exits with a reason on stderr.
If the prompt is answered late, or is hidden behind other windows, the server
misses the window and all Claude Code reports is the timeout. Run `/mcp`,
reconnect `grafana`, and approve the prompt straight away. If no prompt
appears, run `op whoami` yourself to check the CLI is signed in and its
desktop-app integration is on. Don't have Claude run `op read` to test it: the
token would land in the transcript.

Then three reads, in Claude Code:

| Check | Ask for | Expected |
|---|---|---|
| Datasources | the list of datasources | Prometheus and Loki both appear |
| Prometheus | `up` as an instant query | one series per scrape target |
| Loki | a small `{namespace="dns"}` range over a few minutes | log lines returned |

Then confirm the write half: ask Claude to create a dashboard or a folder. The
correct outcome is that it reports **having no such tool** — not that it tried
and got a 403. A 403 would mean `-disable-write` is not in effect and only the
role is protecting you.

Verify the role by looking at it rather than by attempting a write:
**Administration → Users and access → Service accounts** should show
`claude-code` with role `Viewer`.

## Rotation and revocation

Delete the service account in the same UI page. The token dies with it and every
query fails immediately — there is nothing else to unwind. Re-run this runbook
to issue a new one.

## Recording it

The service account is UI-created and not reproducible from a reconcile, so it
is listed in [State That Is Not In Git](../host-state.md). A Grafana rebuild
that replaces the PVC loses it, and the symptom is queries failing with 401.
