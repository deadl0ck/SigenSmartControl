Used to obtain real-time energy flow data of the specified power station, including power of PV, grid, load, energy storage and other links, and energy storage SOC status.

# Access Restriction
One account can only access one device in a station once every five minutes.

# Request Parameter
| Name | Type | Required | Description |
| --- | --- | --- | --- |
| systemId | String | Yes | System unique code |

# Response Message
| Name | Type | Description |
| --- | --- | --- |
| code | Boolean | Error Code (0 means success) |
| msg | String | Message |
| data | Object | The data contains the returned information, including the following: |
| >> pvPower | Double | PV generation power (unit: kW) |
| >> gridPower | Double | Grid power (positive is selling to grid and negative is buying from grid, unit: kW) |
| >> evPower | Double | DC or AC power (unit: kW) |
| >> loadPower | Double | Used by load (unit: kW) |
| >> heatPumpPower | Double | Heat pump power (unit: kW) |
| >> batteryPower | Double | Battery power (positive is import to batteries, and negative is export from battery, unit: kW) |
| >> batterySoc | Double | Battery soc (unit: %) |

# Sample Request
```json
{
    "systemId" : "NDXZZ1731665796"
}
```

# Sample Successful Response
```json
{
    "code": 0,
    "msg": "success",
    "data": {
        "pvPower": 10.1,
        "gridPower": 10.1,
        "evPower": 0,
        "loadPower": 0,
        "heatPumpPower": 0,
        "batteryPower": 0,
        "batterySoc": 100
    }
}
```

# Sample Failed Response
```json
{
    "code": 1000,
    "msg": "param illegal",
    "data": null
}
```