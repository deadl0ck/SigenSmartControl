# Introduction
Before accessing the Openapi service, users need to apply to technical staff for an authorization code. Each time they call the API, they must use this authorization code to request a token.

When a client application requests a (client) token from the authorization server, the request must include specific parameters and be encoded in the "application/x-www-form-urlencoded" format.

If a third-party user program wants to retrieve data from Sigenergy Cloud, it must implement the authentication and authorization process, which includes the following details:
- Sigenergy Cloud will grant third-party users a client ID (client_id) and a client secret (client_secret) so that they can obtain tokens in combination with the standard OAuth2 client authorization types.
- The third-party user program needs to regularly obtain the authorization decision from the resource owner. Only after the authorization is approved can it access user resources.
- The default expiration time for a token is 12 hours. Once the token expires, you need to obtain a new token again through the client authorization type.