# Trigger Detection Tests

Run each test input in Claude Code and verify the expected skill activates.

## data-analysis

| Test Input | Expected | Pass? |
|-----------|----------|-------|
| "Analyze this CSV file" | data-analysis activates | |
| "What patterns do you see in this data?" | data-analysis activates | |
| "Run statistics on sales.csv" | data-analysis activates | |
| "Summarize the metrics in this dataset" | data-analysis activates | |
| "/data-analysis metrics.json" | data-analysis activates (direct) | |

## api-doc-generator

| Test Input | Expected | Pass? |
|-----------|----------|-------|
| "Generate API documentation" | api-doc-generator activates | |
| "Document the endpoints in this service" | api-doc-generator activates | |
| "Create a swagger-style API reference" | api-doc-generator activates | |
| "/api-doc-generator src/routes/" | api-doc-generator activates (direct) | |

## incident-response

| Test Input | Expected | Pass? |
|-----------|----------|-------|
| "We have a production incident" | Should NOT auto-activate (disable-model-invocation) | |
| "The API is down" | Should NOT auto-activate | |
| "/incident-response" | incident-response activates (direct only) | |

## Negative Tests

| Test Input | Expected | Pass? |
|-----------|----------|-------|
| "Write a unit test" | No skill activates | |
| "Refactor this function" | No skill activates | |
| "Fix the login bug" | No skill activates | |
| "Help me set up CI/CD" | No skill activates | |
