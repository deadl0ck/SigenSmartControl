# Access Restriction

1. Password Error Locking Rule: Within a 60-minute period, if a user enters an incorrect password consecutively for five times, the user will be locked out for 3 minutes. When a user is locked due to this rule, the API will return the error code 11002; 
2. Access Rate Limit for Third-Party Platform Users: Each third-party platform user is restricted to a maximum of 10 accesses per minute; 
3. Authorization Rule for Third-Party User Programs: Third-party user programs need to periodically obtain the authorization decision of the resource owner. Only after the authorization is approved can they access user resources.

# Request Parameter
| Name | Type | Required | Description |
| --- | --- | --- | --- |
| username | String | Yes | Username used during user registration |
| password | String | Yes | Password |

# Response Message
| Name | Type | Description |
| --- | --- | --- |
| code | Integer | Error Code |
| msg | String | Message |
| data | String | The data contains the following contents |
| >accessToken | String | Access token |
| >expiresIn | Long | Expiration time of the token |
| >tokenType | String | Token type |

# Sample Request
```json
{
  "username": "test@test.com",
  "password": "kNlsj6voh+Q"
}
```

# Sample Successful Response
```json
{
  "code": 0,
  "msg": "success",
  "data":"{"tokenType":"Bearer","accessToken":"HgrU1Rn2CVUx4rV8C7zpEIFBYz1gUW8kg6VDv9kR5oMtTrzXeKVopndPJWdd9foRSxrqpRGXC5ykHzO5W5pXIhWVsAExbPM3i7p2UplsXTl3ovfoFEwHHZRlJDMFmB","expiresIn":43199}"
}
```

# Sample Failed Response
```json
{
  "code": 11003,
  "msg": "authentication failed",
  "data": null
}
```