# Let's Encrypt Certificates on a UniFi Console

Gives each UDR's web UI a publicly-trusted certificate for its internal name,
so `https://udr.<site>.internal.zerpzorp.com` opens without a browser warning.

UniFi OS issues and renews this itself. There is no declarative path to a UDR
and no reason to build one — a CronJob pushing certs over the UniFi API would
be more code, more secrets and more version fragility for strictly less
reliability than the vendor's own renewal.

Per console, so it is done once at Akron and once at Eastbank.

## What makes this safe to do at all

The console validates over **DNS-01**, using a Cloudflare API token you paste
into the UI. No inbound port 80, no public A record, and the name never has to
resolve from outside — which is why an internal-only name works here at all.

That also means the console holds a Cloudflare token with write access to a
real zone. Treat it the way the cluster's `acme-*` tokens are treated: one
token per console, scoped as tightly as Cloudflare allows, recorded in
1Password, revocable without touching the other one.

## Before you start

- The name must already resolve on the LAN. `udr.<site>.internal.zerpzorp.com`
  is answered by that site's PiHole from `sites/<site>/infrastructure/site.conf`
  — see [naming convention](../naming-convention.md).

  **A site with no K3s cluster here has no PiHole, so it has no answer for the
  name.** DNS-01 does not care — the certificate issues against a name that
  resolves nowhere, and the console will serve it correctly the moment
  something answers. Lottage is in exactly this state (#228 removed its
  scaffolding until the hardware is upgraded), so issuing there now is banking
  the certificate, not gaining a working URL: reaching that console still means
  its IP, and still warns.
- A Cloudflare API token, created per console.

### Cloudflare token

Cloudflare → My Profile → API Tokens → Create Token → Create Custom Token:

| Field | Value |
|---|---|
| Name | `unifi-akron-udr` (or `unifi-eastbank-udr`) |
| Permissions | `Zone` → `DNS` → `Edit`, and `Zone` → `Zone` → `Read` |
| Zone Resources | Include → Specific zone → `zerpzorp.com` |

Same scope as the cluster's `acme-<site>` token, which cert-manager uses for
the wildcard — UniFi's solver needs nothing extra. A separate token per console
anyway, so one can be revoked without taking out the other three.

**The token is scoped to `zerpzorp.com`, the apex, not to
`internal.zerpzorp.com`** — the same compromise the cluster's `acme-*` tokens
make, and for the same reason: `internal.` is a set of records inside the
`zerpzorp.com` zone today, not a separately delegated zone, and Cloudflare
scopes tokens by zone. `docs/naming-convention.md` explains why `internal.`
exists and what the tighter scope would buy; the zone split and every place
that assumes the current shape are tracked in #314.

Store it in 1Password before leaving the page — Cloudflare shows the value
once.

## Issue the certificate

UniFi Network → Settings → Control Plane → Console → **Certificates** → Add New
(`/network/default/settings/control-plane/console`):

| Field | Value |
|---|---|
| Let's Encrypt / Upload | **Let's Encrypt** |
| Name | `akron-udr` |
| Domain Name | `udr.akron.internal.zerpzorp.com` |
| Manual DNS Setup | **unchecked** — leave it to the API token |
| DNS Provider | Cloudflare |
| API Token | the token above |

Then **Add**. Issuance takes up to 15 minutes.

**Issued is not in use.** The certificate appears in the Certificates list with
a green `Valid` tick while the console is still serving its self-signed one —
the `Status` column is the switch. Click **Activate**; it then reads
`Deactivate`, which is the steady state and means this certificate is live.
Skipping it leaves a valid, renewing, entirely unused certificate and a browser
warning that looks like the issuance failed.

`Name` is a label for the certificate entry in this list, not a hostname. It
does not rename the console and it does not appear in DNS — see *the UDR
cannot be renamed* in [naming convention](../naming-convention.md).

## Verify

From a client on the LAN:

```sh
curl -vI https://udr.akron.internal.zerpzorp.com 2>&1 | grep -E 'subject|issuer|SSL certificate'
```

Expect the subject to match the name and the issuer to be Let's Encrypt, with
`SSL certificate verify ok`. A browser should show no warning.

Reaching the console by IP (`https://10.6.1.1`) still warns, and always will —
a certificate cannot cover a bare IP here. Use the name.

## Renewal

UniFi renews it, using the stored token. Nothing to schedule.

The failure mode is the token, not the certificate: revoke or expire the
Cloudflare token and renewal fails silently ~60 days later. Nothing in this
repo watches for that — the console's own certificate is outside everything
Prometheus scrapes. It is recorded in
[State That Is Not In Git](../host-state.md) for that reason.

## Rebuilding a console

Nothing here is restored by a reconcile. After a factory reset or a
replacement device, redo this page from the top with a fresh token, and revoke
the old one in Cloudflare.
