// mappingFile.js — read a user-supplied mapping file and complete it into a
// MappingReport for POST /mappings/confirm.
//
// Accepted shapes (JSON):
//   - a mapping report:     { "proposed_mappings": [...], ... }
//     (what `churn-survival map <file>` drafts, or a hand-written file)
//   - a confirmed mapping:  { "report": { "proposed_mappings": [...] }, ... }
//     (a config/mappings/map_*.json copied from another deployment)
//
// The dataset's own fingerprint always comes from the server-drafted skeleton;
// the server re-binds it on confirm, so a file written for another export can be
// reused as long as its source columns exist in this dataset.

export async function readMappingFile(file) {
  let parsed;
  try {
    parsed = JSON.parse(await file.text());
  } catch {
    throw new Error(`${file.name} is not valid JSON.`);
  }
  const report = parsed && typeof parsed === "object" && parsed.report ? parsed.report : parsed;
  if (!report || !Array.isArray(report.proposed_mappings)) {
    throw new Error(
      `${file.name} is not a mapping file: expected "proposed_mappings" ` +
        `(a mapping report) or "report.proposed_mappings" (a confirmed mapping).`,
    );
  }
  report.proposed_mappings.forEach((mapping, index) => {
    if (!mapping || typeof mapping.source_column !== "string" || typeof mapping.target_field !== "string") {
      throw new Error(
        `${file.name}: proposed_mappings[${index}] needs "source_column" and "target_field".`,
      );
    }
  });
  return report;
}

// Merge a parsed mapping file onto the server skeleton for this dataset. Only
// MappingReport fields are kept (the API rejects unknown fields).
export function mappingFromFile(skeleton, parsed, fileName) {
  const proposed = parsed.proposed_mappings.map((mapping) => ({
    source_column: mapping.source_column,
    target_field: mapping.target_field,
    confidence: typeof mapping.confidence === "number" ? mapping.confidence : 1,
    transformation: mapping.transformation || "identity",
    notes: mapping.notes ?? null,
  }));
  const extras = Array.isArray(parsed.suggested_extra_features)
    ? parsed.suggested_extra_features
        .filter((extra) => extra && extra.source && extra.suggested_key)
        .map((extra) => ({ source: extra.source, suggested_key: extra.suggested_key }))
    : [];
  const used = new Set([
    ...proposed.map((mapping) => mapping.source_column),
    ...extras.map((extra) => extra.source),
  ]);
  return {
    source_fingerprint: skeleton.source_fingerprint,
    proposed_mappings: proposed,
    unmapped_columns: skeleton.source_fingerprint.column_names.filter((c) => !used.has(c)),
    suggested_extra_features: extras,
    data_quality_flags: Array.isArray(parsed.data_quality_flags) ? parsed.data_quality_flags : [],
    recommended_action: parsed.recommended_action || "create_deterministic_adapter",
    llm_model_used: `uploaded file: ${fileName}`,
    generated_at: skeleton.generated_at,
  };
}
