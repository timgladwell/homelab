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
    echo "self-test ok"
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

    echo "hostname: $(hostnamectl --static)"
    echo "next: restart k3s, then delete the old Node object — see docs/runbooks/node-rename.md"
}

main "$@"
