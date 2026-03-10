---
name: api-doc-generator
description: >
  Generate comprehensive API documentation from source code.
  Use when creating endpoint docs, API references, or swagger-style documentation.
  Scans route definitions, extracts parameters, and produces structured docs.
allowed-tools: Read, Grep, Glob, Bash(python *), Write
---

# API Documentation Generator

## Step 1: Discover Endpoints

- Use Grep and Glob to find route/endpoint definitions
- Common patterns to search for:
  - Express: `router.get`, `router.post`, `app.get`, `app.post`
  - FastAPI: `@app.get`, `@app.post`, `@router.get`
  - Django: `path(`, `url(`
  - Flask: `@app.route`, `@blueprint.route`
  - Spring: `@GetMapping`, `@PostMapping`, `@RequestMapping`
- Build a complete endpoint inventory

## Step 2: Extract Details

For each endpoint, extract:
- HTTP method and path
- Request parameters (path, query, body)
- Request/response types or schemas
- Response format and status codes
- Authentication requirements
- Rate limits if documented
- Middleware or decorators applied

## Step 3: Generate Documentation

- Follow the style guide in
  [references/style-guide.md](references/style-guide.md)
- Use the example in [examples/sample-api-doc.md](examples/sample-api-doc.md)
  as a formatting reference
- Group endpoints by resource/domain
- Include request/response examples with realistic data
- Document error responses

## Step 4: Extract Live Data (Optional)

If a running server is accessible, optionally run:

```bash
python ${CLAUDE_SKILL_DIR}/scripts/extract-endpoints.py --source <src-dir>
```

## Output

Write documentation to `docs/api-reference.md` unless the user
specifies a different location.
