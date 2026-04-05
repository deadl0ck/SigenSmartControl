Used to obtain real-time summary data of the specified power station system, including daily, monthly, annual and cumulative power generation, as well as cumulative emission reduction related data.

# Access Restriction
One account can only access one station once every five minutes.

# Request Parameter
| Name | Type | Required | Description |
| --- | --- | --- | --- |
| systemId | String | Yes | System unique code |

# Response Message
| Name                    | Type   | Description                                 |
| ----------------------- | ------ | ------------------------------------------- |
| dailyPowerGeneration    | Double | Daily PV energy generation (kWh)            |
| monthlyPowerGeneration  | Double | Monthly PV energy generation (kWh)          |
| annualPowerGeneration   | Double | Annual PV energy generation (kWh)           |
| lifetimePowerGeneration | Double | Lifetime PV energy generation (kWh)         |
| lifetimeCo2             | Double | Lifetime CO₂ emission reduction (tons)      |
| lifetimeCoal            | Double | Lifetime standard coal saved (tons)         |
| lifetimeTreeEquivalent  | Double | Lifetime equivalent number of trees planted |

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
    "timestamp": 1757581478,
    "data": {
        "dailyPowerGeneration": 0.0,
        "monthlyPowerGeneration": 0.0,
        "annualPowerGeneration": 1394.37,
        "lifetimePowerGeneration": 1394.38,
        "lifetimeCo2": 0.66,
        "lifetimeCoal": 0.56,
        "lifetimeTreeEquivalent": 0.9
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