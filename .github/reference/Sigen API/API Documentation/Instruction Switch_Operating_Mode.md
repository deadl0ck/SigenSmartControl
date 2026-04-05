Set the energy storage operating mode of the corresponding system

# Notes

* Data exchange uses JSON format.
* In the Northbound scenario, this interface requires authorization.

# Request Parameters

| Name                       | Type    | Required | Description                                                                                                                                     |
| -------------------------- | ------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| systemId                   | String  | Yes      | Unique identifier of the system                                                                                                                 |
| energyStorageOperationMode | Integer | No       | Energy storage operating mode. Refer to [Operational Mode Enum](https://developer.sigencloud.com/user/api/document/60) |

# Request Example

```json
{
    "systemId": "NDXZZ1731665796",
    "energyStorageOperationMode": 0
}
```
