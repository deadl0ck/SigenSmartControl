Query the current energy storage operating mode of a power station

# Access Restrictions

Each account can access a single power station only once every five minutes.

# Request Parameters

| Name     | Type   | Description                     |
| -------- | ------ | ------------------------------- |
| systemId | String | Unique identifier of the system |

# Response Information

| Name                        | Type    | Description                                                                                                                              |
| --------------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| code                        | Integer | Error code                                                                                                                               |
| msg                         | String  | Message                                                                                                                                  |
| data                        | Object  | Detailed setting information as follows                                                                                                  |
| >energyStorageOperationMode | Integer | Energy storage operating mode. Refer to [Operational Mode Enum](https://developer.sigencloud.com/user/api/document/60) |

# Successful Response Example

```json
{
    "code": 0,
    "msg": "success",
    "data": {
        "energyStorageOperationMode": 0
    }
}
```

# Failed Response Example

```json
{
    "code": 1000,
    "msg": "param illegal",
    "data": false
}
```
