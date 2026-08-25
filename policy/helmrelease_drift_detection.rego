package homelab.helmreleasedrift

import future.keywords.if

# helm-controller decides a release is in sync by comparing the Helm release
# revision, not the objects that release created. So any out-of-band change to a
# rendered resource is invisible: Flux keeps reporting Ready, and Helm's
# three-way merge actively preserves the change, because it only patches fields
# that differ between chart revisions.
#
# Akron's kube-state-metrics sat at replicas: 0 for fifteen days that way. The
# site that stores all the observability data had no kube_* metrics at all, no
# alert fired, and every reconcile in that window reported success.
#
# driftDetection makes helm-controller diff the live objects instead. Requiring
# it here rather than trusting people to remember is the same reasoning as
# pihole_sync_clients.rego: the failure mode is silence, and a HelmRelease added
# later would reintroduce the blind spot for that component with no signal.
deny contains msg if {
	input.kind == "HelmRelease"
	not input.spec.driftDetection.mode
	msg := sprintf(
		"HelmRelease %q does not set spec.driftDetection.mode — without it helm-controller compares the release revision, not the live objects, so an out-of-band change is silently preserved. Use mode: warn.",
		[input.metadata.name],
	)
}

# `enabled` corrects drift automatically. That is deliberately not used here: it
# hides the fact that someone is making manual changes, which is the signal
# worth having, and it risks reverting a legitimate out-of-band write —
# kube-prometheus-stack's admission webhook caBundle is injected by a Job after
# Helm renders it empty, so correcting that particular drift would break
# Prometheus and PrometheusRule admission.
#
# If a release ever genuinely needs correction, this rule is the place to record
# why, alongside the driftDetection.ignore paths that make it safe.
deny contains msg if {
	input.kind == "HelmRelease"
	input.spec.driftDetection.mode == "enabled"
	msg := sprintf(
		"HelmRelease %q sets driftDetection.mode: enabled — correcting drift automatically hides manual changes and can revert legitimate out-of-band writes. Use mode: warn, or add an exception to policy/helmrelease_drift_detection.rego with the driftDetection.ignore paths that make correction safe.",
		[input.metadata.name],
	)
}
