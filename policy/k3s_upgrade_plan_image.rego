package homelab.rendered

import future.keywords.if
import future.keywords.in

# A system-upgrade-controller Plan's jobs run privileged and replace the k3s
# binary, but the controller creates them at runtime, so no rule on pod specs
# sees their image. These rules check it on the Plan instead (#705).
#
# Pinned by digest, for the same reason as any privileged image: a re-pushed tag
# can't change what runs as root on the node.
#
# Tag in step with `version:`, because the controller marks the Plan done at
# `version:` whatever the image installed. A PR that bumps `version:` but not the
# image would run the old k3s and report the upgrade complete. Renovate groups
# the two (the `k3s` packageRule in renovate.json); this fails a PR where they
# drift anyway. Image tags spell the release `v1.37.2-k3s1` where `version:`
# has `v1.37.2+k3s1`.
#
# In homelab.rendered so step 8 evaluates it against the kustomize build, where
# the Plans are.

plan_images contains [stage, input.spec[stage].image] if {
	input.kind == "Plan"
	some stage in ["prepare", "upgrade"]
	input.spec[stage].image
}

deny contains msg if {
	some [stage, image] in plan_images
	not contains(image, "@sha256:")
	msg := sprintf(
		"Plan %q runs its privileged %s job from %q without a digest pin — pin it as repo:tag@sha256:… (policy/k3s_upgrade_plan_image.rego)",
		[input.metadata.name, stage, image],
	)
}

deny contains msg if {
	some [stage, image] in plan_images
	want := replace(input.spec.version, "+", "-")
	tag := regex.find_all_string_submatch_n(`^[^@]+:([^@:]+)(@|$)`, image, 1)
	not tag[0][1] == want
	msg := sprintf(
		"Plan %q %s image %q is not tagged %q, its version: %q — the job would install a different k3s than the Plan reports (policy/k3s_upgrade_plan_image.rego)",
		[input.metadata.name, stage, image, want, input.spec.version],
	)
}
