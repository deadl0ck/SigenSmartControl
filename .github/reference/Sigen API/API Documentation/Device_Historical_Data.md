# Device Historical Data

| Type | Description |
| --- | --- |
| Request Method | GET |
| Access Restriction | One account can only access one device in a station once every five minutes. |

# Request Parameter
| Name | Type | Required | Description |
| --- | --- | --- | --- |
| level | String | Yes | History statistics data level. [Enum Data](https://developer.sigencloud.com/user/api/document/34) |
| date | String | Optional | Date, format: yyyy-MM-dd.<br><br>Day, Month, Year is required, and Lifetime is not required. |

# Response Message
| Name | Type | Description |
| --- | --- | --- |
| code | Integer | Error Code |
| msg | String | Message |
| data | Object | The data contains the returned information, including the following. |
| >systemId | String | System unique code |
| >serialNumber | String | Device serial number |
| >deviceType | String | Device type |
| >itemList | List | Data at Each Time Point including the following. |

## Inverter History Data
| Key | Description | Unit | Data Type |
| --- | --- | --- | --- |
| activePower | Real-time active power output | kW  | Numeric |
| reactivePower | Real-time reactive power | kW  | Numeric |
| aPhaseVoltage | Voltage of phase A | V   | Numeric |
| bPhaseVoltage | Voltage of phase B | V   | Numeric |
| cPhaseVoltage | Voltage of phase C | V   | Numeric |
| aPhaseCurrent | Current flowing through phase A | A   | Numeric |
| bPhaseCurrent | Current flowing through phase B | A   | Numeric |
| cPhaseCurrent | Current flowing through phase C | A   | Numeric |
| powerFactor | Ratio of active power to apparent power |  -  | Numeric |
| gridFrequency | Frequency of the electric grid | Hz  | Numeric |
| pVPower | Total power produced by solar panels | kW  | Numeric |
| pV1Voltage | Voltage of PV string 1 | V   | Numeric |
| pV1Current | Current from PV string 1 | A   | Numeric |
| pV2Voltage | Voltage of PV string 2 | V   | Numeric |
| pV2Current | Current from PV string 2 | A   | Numeric |
| pV3Voltage | Voltage of PV string 3 | V   | Numeric |
| pV3Current | Current from PV string 3 | A   | Numeric |
| pV4Voltage | Voltage of PV string 4 | V   | Numeric |
| pV4Current | Current from PV string 4 | A   | Numeric |

## Battery History Data
| Key | Description | Unit | Data Type |
| --- | --- | --- | --- |
| batterySOC | State of Charge of the Battery | %   | Numeric |
| chargingDischargingPower | Power being consumed or supplied to the battery | kW  | Numeric |
| chargeEnergy | Battery charging energy. | kWh | Numeric |
| dischargeEnergy | Battery discharging energy. | kWh | Numeric |

# Sample Request
```json
"systemId" : "NDXZZ1731665796"
"serialNumber" : "110A118B0028s"

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
    "systemId": "NDXZZ1731665796",
    "serialNumber": "110A118B0028",
    "deviceType": "Inverter",
    "itemList": [{
    "dataTime": "2024-04-02 00:00"
    }]}
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
