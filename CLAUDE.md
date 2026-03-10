# Project Configuration

## Skills

This project packages domain expertise as skills in `.claude/skills/`.
Each skill is a self-contained directory with a SKILL.md entrypoint.

Skills are discovered automatically. Use `/skill-name` to invoke directly,
or describe what you need and the matching skill activates based on its description.

### Available Domain Skills

- **data-analysis**: Statistical analysis workflows with consistent methodology
- **api-doc-generator**: Generate API documentation from source code
- **incident-response**: Structured incident response and post-mortem generation (manual only)

### Creating New Skills

Copy `.claude/skills/_template/` to `.claude/skills/your-skill-name/` and
edit the SKILL.md. See the template for all available frontmatter fields.
