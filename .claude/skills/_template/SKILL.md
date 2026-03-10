---
# REQUIRED: Unique identifier. Lowercase, hyphens only. Becomes /name command.
name: my-skill-name

# RECOMMENDED: Claude uses this to decide when to auto-activate.
# Include action verbs and synonyms users would naturally say.
# Keep under 200 characters.
description: >
  Brief description of what this skill does and when to use it.

# OPTIONAL: Set to true if this skill should only be invoked manually via /name.
# Use for dangerous operations, deployments, or side-effect-heavy workflows.
# disable-model-invocation: true

# OPTIONAL: Set to false to hide from / menu. Use for background knowledge
# that Claude should apply automatically but users shouldn't invoke directly.
# user-invocable: false

# OPTIONAL: Tools Claude can use without asking permission during this skill.
# allowed-tools: Read, Grep, Glob, Bash(python *)

# OPTIONAL: Run in isolated subagent context.
# context: fork

# OPTIONAL: Subagent type when context is fork.
# agent: Explore
---

# Skill Title

Brief overview of what this skill accomplishes.

## Prerequisites

List any requirements (files that must exist, tools that must be available, etc.)

## Workflow

### Step 1: [First Action]

Detailed instructions for the first step.

### Step 2: [Second Action]

Detailed instructions for the second step.
- For detailed reference, read [references/your-reference.md](references/your-reference.md)

### Step 3: [Validation]

Run validation:

```bash
bash ${CLAUDE_SKILL_DIR}/scripts/your-script.sh
```

## Output

Describe the expected output format and location.
See [examples/sample-output.md](examples/sample-output.md) for reference.
