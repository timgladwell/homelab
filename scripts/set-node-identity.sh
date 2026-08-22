#!/usr/bin/env bash
# Set this box's hostname and /etc/hosts to its FQDN, per docs/naming-convention.md.
#
# k3s takes its node name from the hostname, so this is what decides the node's
# identity in the cluster. Run it BEFORE restarting k3s — see
# docs/runbooks/node-rename.md for the full procedure.
#
# Idempotent: /etc/hosts is regenerated wholesale rather than edited, so
# re-running converges instead of accumulating stale entries.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: sudo ./scripts/set-node-identity.sh <fqdn>
       ./scripts/set-node-identity.sh --self-test

  fqdn   e.g. k3s01.akron.internal.zerpzorp.com

Sets the static hostname, regenerates /etc/hosts, and stops cloud-init from
reverting either on the next boot.
EOF
}

# Render the whole file. Debian puts the host's own name on 127.0.1.1, not
# 127.0.0.1, so that it resolves without depending on a real interface address.
render_hosts() {
    local fqdn=$1 short=${1%%.*}
    cat <<EOF
# Managed by scripts/set-node-identity.sh — edits here are overwritten.
127.0.0.1	localhost
127.0.1.1	${fqdn} ${short}

::1		localhost ip6-localhost ip6-loopback
ff02::1		ip6-allnodes
ff02::2		ip6-allrouters
EOF
}

self_test() {
    local out
    out=$(render_hosts k3s01.akron.internal.zerpzorp.com)
    grep -qx '127.0.1.1	k3s01.akron.internal.zerpzorp.com k3s01' <<<"$out"
    # Regenerating from its own output must not drift.
    [[ "$out" == "$(render_hosts k3s01.akron.internal.zerpzorp.com)" ]]
    # A short name must not silently produce "host host".
    ! validate_fqdn k3s01 2>/dev/null
    validate_fqdn k3s01.akron.internal.zerpzorp.com

    # strip_search drops the search list and nothing else — a kubelet
    # resolv.conf that lost its nameservers would break DNS for every pod.
    local stripped
    stripped=$(printf '# comment\nsearch akron.internal.zerpzorp.com\nnameserver 10.6.1.53\nnameserver 1.1.1.1\n' | strip_search)
    [[ "$stripped" != *search* ]]
    [[ $(grep -c '^nameserver ' <<<"$stripped") -eq 2 ]]
    # "searchdomain" is not a search directive and must survive.
    [[ $(printf 'nameserver 1.1.1.1\nsearchdomain foo\n' | strip_search | wc -l) -eq 2 ]]

    echo "self-test ok"
}

# Give kubelet a resolv.conf without the search list.
#
# An FQDN hostname makes NetworkManager derive a search domain from it, and
# kubelet copies the host's search list into every pod. Pods also run with
# ndots:5, which means any name with fewer than five dots tries every search
# suffix BEFORE the name itself — so a pod resolving "pypi.org" first asks for
# "pypi.org.<site>.internal.zerpzorp.com".
#
# That is normally harmless: a suffix that does not exist returns NXDOMAIN and
# the resolver moves on. internal.zerpzorp.com is a real Cloudflare-hosted
# zone, and Cloudflare answers NODATA (NOERROR, no records) instead. glibc
# treats NODATA as "found it, no address" and STOPS — it never tries the real
# name. Every external lookup from every pod fails while the host, at the
# default ndots:1, is unaffected.
#
# Fixed at the kubelet layer rather than by removing the search domain,
# because the host wants to keep it: it is the same suffix #234 adds via DHCP
# so short names work, and once internal.zerpzorp.com resolves for real it
# becomes useful rather than harmful. Pods never type short external names and
# gain nothing from it.
#
# The nameservers are copied from the host rather than restated, so
# NetworkManager stays the single source of truth. This is a snapshot — re-run
# this script after changing the host's resolvers.
# Drop the search list, keep everything else. Separate so the self-test can
# exercise it without writing to /etc.
strip_search() {
    grep -v '^[[:space:]]*search[[:space:]]'
}

configure_kubelet_resolv() {
    local kubelet_resolv=/etc/rancher/k3s/resolv.conf
    local k3s_config=/etc/rancher/k3s/config.yaml

    [[ -d /etc/rancher/k3s ]] || { echo "no /etc/rancher/k3s — skipping kubelet DNS"; return; }

    strip_search < /etc/resolv.conf > "$kubelet_resolv"

    if ! grep -qs 'resolv-conf=' "$k3s_config"; then
        printf 'kubelet-arg:\n  - "resolv-conf=%s"\n' "$kubelet_resolv" >> "$k3s_config"
        echo "added kubelet-arg to $k3s_config"
    fi
}

validate_fqdn() {
    local fqdn=$1
    [[ $fqdn == *.*.* ]] || { echo "not an FQDN: $fqdn" >&2; return 1; }
    [[ $fqdn =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$ ]] \
        || { echo "not a valid DNS name (lowercase, digits, hyphens): $fqdn" >&2; return 1; }
}

main() {
    case ${1:-} in
        --self-test) self_test; return ;;
        -h|--help|'') usage; return 1 ;;
    esac

    local fqdn=$1
    validate_fqdn "$fqdn"
    [[ $EUID -eq 0 ]] || { echo "must run as root" >&2; return 1; }

    render_hosts "$fqdn" > /etc/hosts
    hostnamectl set-hostname "$fqdn"

    # cloud-init is not first-boot-only: set_hostname and update_etc_hosts are
    # both PER_ALWAYS. update_etc_hosts in particular rebuilds /etc/hosts every
    # boot from the fqdn in the seed, silently reverting this script's work.
    #
    # Disabling cloud-init outright beats pinning preserve_hostname and
    # manage_etc_hosts: it is one file instead of two keys, and it covers the
    # modules nobody has audited yet rather than only the two that are known to
    # bite. Its only job on these boxes was first-boot provisioning, which is
    # long finished by the time a node is being renamed. A reflash replaces the
    # root filesystem, so this cannot suppress a genuine re-provision.
    touch /etc/cloud/cloud-init.disabled

    configure_kubelet_resolv

    echo "hostname: $(hostnamectl --static)"
    echo "next: restart k3s, then delete the old Node object — see docs/runbooks/node-rename.md"
    echo "      existing pods keep their old DNS config; they need recreating"
}

main "$@"
