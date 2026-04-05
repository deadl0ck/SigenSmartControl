# System Historical Data

| Type | Description |
| --- | --- |
| Request Method | GET |
| Access Restriction | One account can only access one station once every five minutes. |

# Request Parameter
| Name | Type | Required | Description |
| --- | --- | --- | --- |
| level | String | Yes | History statistics data level. [Enum Data](https://developer.sigencloud.com/user/api/document/34) |
| date | String | Optional | Date, format: yyyy-MM-dd. Day, Month, Year is required, and Lifetime is not required. |

# Response Message
| Name | Type | Description |
| --- | --- | --- |
| code | Integer | Error Code |
| msg | String | Message |
| data | Object | The data contains the returned information, including the following. |
| >powerGeneration | Double | Total green power generation. |
| >powerToGrid | Double | Green power sold to the grid. |
| >powerSelfConsumption | Double | Self-consumed green power. |
| >powerUse | Double | Total load power consumption. |
| >powerFromGrid | Double | Non-green power purchased from the grid. |
| >powerOneself | Double | Load green power consumption. |
| >esCharging | Double | Total battery charging energy. |
| >esDischarging | Double | Total battery discharging energy. |
| >itemList | List | Data at Each Time Point including the following. |
| >>dataTime | String | Data timestamp. |
| >>pvTotalPower | Double | Solar real-time power output. |
| >>loadPower | Double | Household load real-time power draw. |
| >>toGridPower | Double | Realtime power exported to the grid. |
| >>fromGridPower | Double | Realtime power imported from the grid. |
| >>esChargeDischargePower | Double | Battery charge/discharge real-time power. |
| >>esChargePower | Double | Battery charging real-time power. |
| >>esDischargePower | Double | Battery discharging real-time power. |
| >>oneselfPower | Double | Self-sufficiency real-time power usage. |
| >>powerGeneration | Double | Total green power generation. |
| >>powerToGrid | Double | Green power sold to the grid. |
| >>powerSelfConsumption | Double | Self-consumed green power. |
| >>powerUse | Double | Total load power consumption. |
| >>powerFromGrid | Double | Non-green power purchased from the grid. |
| >>powerOneself | Double | Load green power consumption. |
| >>powerFromBattery | Double | Power drawn from the battery. |
| >>esCharging | Double | Battery charging energy. |
| >>esDischarging | Double | Battery discharging energy. |
| >>batSoc | Double | Battery State of Charge (available on a day-level basis only). |

# Sample Request
```json
"systemId" : "NDXZZ1731665796"
{
    "level" : "Day",
    "date" : "2024-06-27"
}
```

# Sample Successful Response
```json
{
    "code": 0,
    "msg": "success",
    "data": {
    "powerGeneration": 22.94,
    "powerToGrid": 3.93,
    "powerSelfConsumption": 19.01,
    "powerUse": 24.83,
    "powerFromGrid": 9.67,
    "powerOneself": 15.16,
    "esCharging": 6.51,
    "esDischarging": 0.47,
    "itemList": [{
        "dataTime": "2024-04-02 00:00",
        "pvTotalPower": 0.0,
        "loadPower": 1.06,
        "toGridPower": 0.0,
        "fromGridPower": 0.0,
        "esChargeDischargePower": -1.016,
        "esChargePower": 0.0,
        "esDischargePower": 1.016,
        "oneselfPower": 1.06,
        "powerGeneration": 0.0,
        "powerToGrid": 0.0,
        "powerSelfConsumption": 0.0,
        "powerUse": 0.01,
        "powerFromGrid": 0.0,
        "powerOneself": 0.01,
        "powerFromBattery": 0.0,
        "esCharging": 0.0,
        "esDischarging": 0.0,
        "batSoc": 20.1
    }]
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