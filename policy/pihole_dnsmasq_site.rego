package homelab.piholednsmasq

import future.keywords.if

# The pihole-dnsmasq ConfigMap is assembled from two files: the global
# directives in base/dns/dnsmasq-base.conf, and the site's own site.conf merged
# in by sites/<site>/infrastructure/.
#
# A site that forgets the merge gets a PiHole with only the base records — a
# quiet, partial DNS outage that looks like a working cluster. Same shape as
# policy/pihole_sync_clients.rego: catch the missing key at PR time.
deny contains msg if {
	input.kind == "ConfigMap"
	startswith(input.metadata.name, "pihole-dnsmasq-")
	not input.data["site.conf"]
	msg := sprintf(
		"ConfigMap %q is missing site.conf — the site's infrastructure kustomization must merge its own dnsmasq records into the pihole-dnsmasq ConfigMap",
		[input.metadata.name],
	)
}
