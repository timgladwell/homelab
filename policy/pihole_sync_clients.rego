package homelab.piholesync

import future.keywords.if

# The pihole-sync ConfigMap is assembled from two files: the global config in
# base/pihole-sync/, and the site's own pihole-clients.yaml merged in by
# sites/<site>/dns-config/.
#
# If a site forgets the merge, sync.py sees no clients file and dies — but only
# at Job runtime, on the cluster, after the change has already shipped. Worse,
# if the file were ever made optional, "no clients desired" would read as
# "delete every client this site has". Catch the missing key at PR time.
deny contains msg if {
	input.kind == "ConfigMap"
	startswith(input.metadata.name, "pihole-sync-")
	not input.data["pihole-clients.yaml"]
	msg := sprintf(
		"ConfigMap %q is missing pihole-clients.yaml — the site's dns-config kustomization must merge its own clients file into the pihole-sync ConfigMap",
		[input.metadata.name],
	)
}
