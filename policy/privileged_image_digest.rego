package homelab.rendered

import future.keywords.if
import future.keywords.in

# A privileged container is root on its node: everything the node can reach,
# every Secret in the cluster, and the site's DNS. Its image is pinned by
# digest (`repo:tag@sha256:…`), so a re-pushed or hijacked tag can't change what
# runs with that access; Renovate still bumps tag and digest together (its
# customManagers in renovate.json capture the digest).
#
# What a pin does not do: it guards against an image being swapped in under a
# tag we already trust, not against the image being malicious. A digest
# Renovate proposes is whatever the registry served at that moment, so a
# compromised upstream release is pinned as faithfully as a good one. Trust in
# the content still rests on the publisher and on reviewing the bump.
#
# In homelab.rendered so both steps evaluate it: step 15 against chart-rendered
# objects, and step 8, which runs every namespace, against the kustomize build.
# No workload today runs a privileged container: #702 weighed a privileged SMART
# exporter and chose a sandboxed host timer instead. The pod rule is here for
# the next one; the Plan rules below cover the k3s upgrade jobs now.

pod_spec(obj) := obj.spec.template.spec if {
	obj.kind in {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"}
}

pod_spec(obj) := obj.spec.jobTemplate.spec.template.spec if obj.kind == "CronJob"

pod_spec(obj) := obj.spec if obj.kind == "Pod"

deny contains msg if {
	spec := pod_spec(input)
	some c in array.concat(object.get(spec, "containers", []), object.get(spec, "initContainers", []))
	c.securityContext.privileged == true
	not contains(c.image, "@sha256:")
	msg := sprintf(
		"%s %q runs privileged container %q from %q without a digest pin — pin it as repo:tag@sha256:… (policy/privileged_image_digest.rego)",
		[input.kind, input.metadata.name, c.name, c.image],
	)
}

# A system-upgrade-controller Plan's jobs run privileged and replace the k3s
# binary, but the controller creates them at runtime, so the pod rule above
# never sees them. These check the image on the Plan instead (#705).
#
# Its tag must also match `version:`, because the controller marks the Plan done
# at `version:` whatever the image installed: a PR bumping `version:` but not the
# image would run the old k3s and report the upgrade complete. Renovate groups
# the two (the `k3s` packageRule); this fails a PR where they drift anyway.
# Image tags spell the release `v1.37.2-k3s1` where `version:` has `v1.37.2+k3s1`.

plan_images contains [stage, input.spec[stage].image] if {
	input.kind == "Plan"
	some stage in ["prepare", "upgrade"]
	input.spec[stage].image
}

deny contains msg if {
	some [stage, image] in plan_images
	not contains(image, "@sha256:")
	msg := sprintf(
		"Plan %q runs its privileged %s job from %q without a digest pin — pin it as repo:tag@sha256:… (policy/privileged_image_digest.rego)",
		[input.metadata.name, stage, image],
	)
}

deny contains msg if {
	some [stage, image] in plan_images
	want := replace(input.spec.version, "+", "-")
	tag := regex.find_all_string_submatch_n(`^[^@]+:([^@:]+)(@|$)`, image, 1)
	not tag[0][1] == want
	msg := sprintf(
		"Plan %q %s image %q is not tagged %q, its version: %q — the job would install a different k3s than the Plan reports (policy/privileged_image_digest.rego)",
		[input.metadata.name, stage, image, want, input.spec.version],
	)
}
