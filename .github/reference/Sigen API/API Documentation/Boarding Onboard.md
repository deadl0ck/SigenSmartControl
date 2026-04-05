This API is used to onboard a specified system (systemId) into the platform.

# Request Headers

| Header        | Required | Type   | Description                                           |
| ------------- | -------- | ------ | ----------------------------------------------------- |
| Authorization | Yes      | String | Authentication token, format: `Bearer <access_token>` |
| Content-Type  | Yes      | String | `application/json`                                    |


```json
Authorization: Bearer D9gwcA4L6D-j2tM9jnYsYTeDM
Content-Type: application/json
```

# Request Body

Type: `application/json`
Supports sending multiple system IDs in a batch.

**Example:**

```json
[
  "KXGCS1727160960",
  "PGIYT1142977051"
]
```

# Response Body

```json
{
    "code": 0,
    "msg": "success",
    "timestamp": 1757580062,
    "data": [
        {
            "systemId": "KXGCS1727160960",
            "result": true,
            "codeList": [
                0
            ]
        },
        {
            "systemId": "PGIYT1142977051",
            "result": false,
            "codeList": [
                1502
            ]
        }
    ]
}
```

# Response Parameters

| Field     | Type   | Description                                                            |
| --------- | ------ | ---------------------------------------------------------------------- |
| code      | int    | Response status code (0 indicates success, non-zero indicates failure) |
| msg       | string | Response message                                                       |
| timestamp | long   | UNIX timestamp (seconds)                                               |
| data      | array  | Array of onboarding results                                            |
