# Workflow Validation Checklist

For each skill, verify the following items.

## Structural Validation

### All Skills
- [ ] SKILL.md exists and has valid YAML frontmatter
- [ ] `name` field matches directory name
- [ ] `description` is present and under 200 characters
- [ ] All referenced files in `references/`, `scripts/`, `examples/` exist
- [ ] Scripts are executable (`chmod +x`)
- [ ] No broken relative links in SKILL.md

### data-analysis
- [ ] references/methodology.md exists
- [ ] references/output-format.md exists
- [ ] scripts/validate-output.sh exists and is executable
- [ ] examples/sample-report.md exists
- [ ] Sample report passes validate-output.sh

### api-doc-generator
- [ ] references/style-guide.md exists
- [ ] scripts/extract-endpoints.py exists and is executable
- [ ] examples/sample-api-doc.md exists

### incident-response
- [ ] `disable-model-invocation: true` is set in frontmatter
- [ ] references/severity-matrix.md exists
- [ ] references/escalation-paths.md exists
- [ ] scripts/check-status.sh exists and is executable
- [ ] examples/sample-incident.md exists

## Execution Validation

- [ ] Layer 1: Skill appears when asking "What skills are available?"
- [ ] Layer 2: Full instructions load when skill is invoked
- [ ] Layer 3: Supporting files are read when referenced in workflow
- [ ] Workflow steps execute in correct order
- [ ] Output matches expected format from examples/
- [ ] Validation scripts run successfully on valid output
