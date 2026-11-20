export function classifyUnusedArtifact(candidate, optionEnabled = false) {
  const errors = [];
  if (!candidate?.path) errors.push("artifact path is required");
  if (!candidate?.kind) errors.push("artifact kind is required");
  if (!candidate?.beforeDigest) errors.push("artifact baseline digest is required");
  if (!Array.isArray(candidate?.unusedEvidence) || candidate.unusedEvidence.length === 0) {
    errors.push("unused classification requires evidence");
  }
  if (!Number.isInteger(candidate?.activeReferences) || candidate.activeReferences < 0) {
    errors.push("active reference count is required");
  }
  if (errors.length > 0) {
    return {
      status: "INVALID",
      removalAuthorized: false,
      errors,
    };
  }
  if (!optionEnabled) {
    return {
      status: "RETAINED_UNUSED",
      removalAuthorized: false,
      action: "retained",
      errors: [],
    };
  }
  if (candidate.activeReferences > 0 || candidate.requiredByBuild || candidate.requiredByPackage) {
    return {
      status: "RETAINED_REQUIRED",
      removalAuthorized: false,
      action: "retained",
      errors: ["artifact still has an active build, package, or code dependency"],
    };
  }
  return {
    status: "REMOVAL_AUTHORIZED",
    removalAuthorized: true,
    action: "remove",
    errors: [],
  };
}

export function finalizeUnusedArtifactRemoval(candidate, execution) {
  const classification = classifyUnusedArtifact(candidate, true);
  if (!classification.removalAuthorized) return classification;
  const errors = [];
  if (execution?.removed !== true) errors.push("artifact was not removed");
  if (execution?.beforeDigest !== candidate.beforeDigest) {
    errors.push("artifact digest changed before removal");
  }
  if (!execution?.rollback || !execution.rollback.includes(candidate.path)) {
    errors.push("exact rollback path is required");
  }
  if (execution?.buildPassed !== true) errors.push("post-removal build did not pass");
  if (execution?.testsPassed !== true) errors.push("post-removal tests did not pass");
  if (execution?.packagePassed !== true) errors.push("post-removal package did not pass");
  if (execution?.archiveInspectionPassed !== true) {
    errors.push("post-removal archive inspection did not pass");
  }
  return {
    status: errors.length === 0 ? "REMOVED" : "FAILED",
    removalAuthorized: true,
    action: "remove",
    errors,
  };
}
