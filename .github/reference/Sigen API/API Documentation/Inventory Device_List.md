Query all device information under the power station based on the unique power station identifier (systemId).

# Access Restriction
One account can only access one station's device list once every five minutes.

# Request Parameter
| Name | Type | Required | Description |
| --- | --- | --- | --- |
| systemId | String | Yes | System unique code |

# Response Message
| Name | Type | Description |
| --- | --- | --- |
| code | Integer | Error Code |
| msg | String | Message |
| data | List | Return data, The data contains the returned information, including the following: |
| > systemId | String | System unique code |
| > serialNumber | String | Device serial number |
| > deviceType | String | Type of device. Refer to [Enum Data](https://developer.sigencloud.com/user/api/document/31) |
| > status | String | Device status. Refer to [Enum Data](https://developer.sigencloud.com/user/api/document/31) |
| > pn | String | Different PN codes identify different types of system. |
| > firmwareVersion | String | Firmware version. |
| > attrMap | Object | Attribute Object. Refer to the following attribute information for each device: |

## Inverter Attribute
| Key | Description | Unit | Data Type |
| --- | --- | --- | --- |
| ratedActivePower | Designated power output capacity. | kW | Double |
| maxActivePower | Maximum achievable power output. | kW | Double |
| maxAbsorbedPower | Highest power absorption capacity. | kW | Double |
| ratedVoltage | Specified operating voltage. | V | Double |
| ratedFrequency | Prescribed operating frequency. | Hz | Double |
| pvStringNumber | Number of PV strings in the system. | - | Integer |

## Battery Attribute
| Key | Description | Unit | Data Type |
| --- | --- | --- | --- |
| ratedEnergy | Rated Energy Storage Capacity | kWh | Double |
| chargeableEnergy | Maximum Rechargeable Energy | kWh | Double |
| dischargeEnergy | Maximum Dischargeable Energy | kWh | Double |
| ratedChargePower | Rated Charging Power | kW | Double |
| ratedDischargePower | Rated Discharging Power | kW | Double |

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
    "data": [{
        "systemId": "NDXZZ1731665796",
        "serialNumber": "110A118B0028",
        "deviceType": "Inverter",
        "status": "normal",
        "pn": "1711814399",
        "firmwareVersion": "V100R001C22B028",
        "attrMap" : {
        "ratedActivePower": 8.0,
        "maxActivePower": 8.0,
        "maxAbsorbedPower": 8.0,
        "ratedVoltage": 8.0,
        "ratedFrequency": 8.0,
        "pvStringNumber": 8.0
        }
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