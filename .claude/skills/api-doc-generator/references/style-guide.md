# API Documentation Style Guide

## General Principles

- Write for developers who are unfamiliar with the codebase
- Every endpoint must have at least one request/response example
- Use consistent formatting throughout the document
- Group related endpoints under resource headings

## Endpoint Format

Each endpoint section should follow this structure:

```
### METHOD /path/to/endpoint

Brief description of what this endpoint does.

**Authentication**: Required/Optional/None
**Rate Limit**: X requests per minute (if applicable)

#### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|

#### Request Body (if applicable)

JSON schema or example.

#### Response

Status code and response body example.

#### Errors

| Status | Description |
|--------|-------------|
```

## Naming Conventions

- Use the actual path as the heading (not a friendly name)
- Use lowercase for HTTP methods in prose, UPPERCASE in headings
- Use `code formatting` for parameter names, types, and paths

## Examples

- Use realistic but fictional data (no real emails, IDs, etc.)
- Show both success and common error responses
- Include curl examples for complex endpoints
