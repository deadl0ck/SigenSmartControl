Used to query the list of power stations that meet the conditions, supporting filtering by grid connection time.

# Access Restriction
- An account can only access every five minutes.
- If only the grid connection start time is passed in, the grid connection end time will default to the current time. If only the grid connection end time is passed in, the grid connection start time will default to 08:00:00 on January 1, 1970.

# Request Parameter
| Name | Type | Required | Description |
| --- | --- | --- | --- |
| startTime | Long | No | Device grid connect start time, timestamp in seconds. |
| endTime | Long | No | Device grid connect end time, timestamp in seconds. |

# Response Message
| Name | Type | Description |
| --- | --- | --- |
| code | Integer | Error code |
| msg | String | Message |
| data | Object | The data contains the returned information, including the following: |
| > systemId | String | System id. |
| > systemName | String | System name |
| > addr | String | System address |
| > status | String | System operational status |
| > isActivate | Boolean | System activated |
| > onOffGridStatus | String | Grid-Connected and off-Grid Status |
| > timeZone | String | System time zone |
| > gridConnectTime | Long | Grid Connection Time |
| > pvCapacity | Integer | PV capacity |
| > batteryCapacity | Integer | Battery capacity |

# Sample Request
```json
{
    "startTime" : "1711468800",
    "endTime" : "1711814399"
}
```

# Sample Successful Response
```json
{
    "code": 0,
    "msg": "success",
    "data": [{
        "systemId": "NDXZZ1731665796",
        "systemName": "pfh24",
        "addr": "*** Shanghai China",
        "status": "normal",
        "isActivate": true,
        "onOffGridStatus": "onGrid",
        "timeZone": "Asia/Shanghai",
        "gridConnectTime": 1711814399,
        "pvCapacity": 80,
        "batteryCapacity": 8
    }]
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