# API Reference

## Users

### GET /api/users

List all users with optional filtering.

**Authentication**: Required (Bearer token)

#### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| page | query | integer | No | Page number (default: 1) |
| limit | query | integer | No | Items per page (default: 20, max: 100) |
| role | query | string | No | Filter by role: `admin`, `user`, `viewer` |

#### Response

**200 OK**

```json
{
  "data": [
    {
      "id": "usr_abc123",
      "email": "jane@example.com",
      "name": "Jane Smith",
      "role": "admin",
      "created_at": "2025-01-15T09:30:00Z"
    }
  ],
  "pagination": {
    "page": 1,
    "limit": 20,
    "total": 142
  }
}
```

#### Errors

| Status | Description |
|--------|-------------|
| 401 | Missing or invalid authentication token |
| 403 | Insufficient permissions |

---

### POST /api/users

Create a new user.

**Authentication**: Required (Bearer token, admin role)

#### Request Body

```json
{
  "email": "john@example.com",
  "name": "John Doe",
  "role": "user"
}
```

#### Response

**201 Created**

```json
{
  "id": "usr_def456",
  "email": "john@example.com",
  "name": "John Doe",
  "role": "user",
  "created_at": "2025-03-10T14:22:00Z"
}
```

#### Errors

| Status | Description |
|--------|-------------|
| 400 | Invalid request body or missing required fields |
| 401 | Missing or invalid authentication token |
| 403 | Requires admin role |
| 409 | Email already exists |

---

### GET /api/users/:id

Retrieve a single user by ID.

**Authentication**: Required (Bearer token)

#### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| id | path | string | Yes | User ID (e.g., `usr_abc123`) |

#### Response

**200 OK**

```json
{
  "id": "usr_abc123",
  "email": "jane@example.com",
  "name": "Jane Smith",
  "role": "admin",
  "created_at": "2025-01-15T09:30:00Z"
}
```

#### Errors

| Status | Description |
|--------|-------------|
| 401 | Missing or invalid authentication token |
| 404 | User not found |
