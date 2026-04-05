# Authentication

![auth](https://sigen-data-cn-public.s3.cn-northwest-1.amazonaws.com.cn//auth-platform.jpg)

In the **Control Center → Settings** page, you can obtain the **Application Key (App Key)** and **Application Secret (App Secret)**.
The platform follows the **OAuth2 standard protocol, Client Credentials Grant type** for authentication.

You need to concatenate `AppKey:AppSecret`, encode it with Base64: `base64(AppKey:AppSecret)`; Then the result would be placed in the request body field `key`.
Then send a request to the authentication server’s Token endpoint to obtain the access token (`accessToken`) required for calling subsequent APIs.

---

# Access Limitations

For third-party platform users, the access frequency is limited to **a maximum of 10 requests per minute**.

---

# Request Parameters

| Name | Type   | Required | Description                             |
| ---- | ------ | -------- | --------------------------------------- |
| key  | String | Yes      | Obtain it by requesting from Sigenergy. |

---

# Response Message

| Name         | Type    | Description      |
| ------------ | ------- | ---------------- |
| code         | Integer | Error code       |
| msg          | String  | Message          |
| data         | String  | Data, includes:  |
| >accessToken | String  | Access token     |
| >expiresIn   | Long    | Token expiration |
| >tokenType   | String  | Token type       |

---

# Request Body Example

```json
{
  "key": "aslsjsvoh9Qq0"
}
```

---

# Successful Response Example

```json
{
  "code": 0,
  "msg": "success",
  "data":"{\"tokenType\":\"Bearer\",\"accessToken\":\"HgrU1Rn2CVUx4rV8C7zpEIFBYz1gUW8kg6VDv9kR5oMtTrzXeKVopndPJWdd9foRSxrqpRGXC5ykHzO5W5pXIhWVsAExbPM3i7p2UplsXTl3ovfoFEwHHZRlJDMFmB\",\"expiresIn\":43199}"
}
```

---

# Failed Response Example

```json
{
  "code": 11003,
  "msg": "authentication failed",
  "data": null
}
```
