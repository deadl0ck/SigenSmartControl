[Switch Operating Mode](https://developer.sigencloud.com/user/api/document/57) — MQTT version, supports switching the operating mode of the energy storage system.

# Notes

* Data exchange uses JSON format.
* In the Northbound scenario, this interface requires authorization.

# Request Format

| Name        | Type   | Required | Description                                                                                                                                     |
| ----------- | ------ | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| accessToken | String | Yes      | Authorization token obtained from the Chapter 2 interface                                                                                       |
| systemId    | String | Yes      | Unique identifier of the power station                                                                                                          |
| mode        | String | Yes      | Energy storage operating mode. Refer to [Operational Mode Enum](https://developer.sigencloud.com/user/api/document/60) |

# Request Example

```json
{
    "accessToken": "JFf_QTaUkGTD9AQXiMrfLBUfM3v2qyLPr3KOole",
    "systemId": "CBFGA1627168228",
    "mode": "NBI"
}
```
