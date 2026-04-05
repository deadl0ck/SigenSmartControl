This interface is used to obtain real-time operation data of a specified device under a specified power station, supporting real-time information query for device types such as AIO, batteries, gateways, and meters.

# Access Restriction
One account can only access one device in a station once every five minutes.

# Request Parameters
| Parameter Name    | Type   | Required | Description               |
|-------------------|--------|----------|---------------------------|
| systemId          | String | Yes      | Unique identifier of the power station |
| serialNumber      | String | Yes      | Device serial number       |

# Response Parameters
| Parameter Name    | Type   | Description               |
|-------------------|--------|---------------------------|
| code              | Boolean| Error code                |
| msg               | String | Prompt message            |
| data              | Object | Returned data, including the following content |
| > systemId        | String | Unique identifier of the power station |
| > serialNumber    | String | Device serial number       |
| > deviceType      | String | Device type               |
| > realTimeInfo    | Object | Device real-time information (different fields for different device types) |

# Real-time Data Fields by Device Type

## AIO Realtime Data

| Key                  | Description                                                                 | Unit | Data Type      |
| -------------------- | --------------------------------------------------------------------------- | ---- | -------------- |
| activePower          | Real-time active power output (positive: generation, negative: consumption) | kW   | Numeric        |
| reactivePower        | Real-time reactive power (positive: generation, negative: consumption)      | kW   | Numeric        |
| aPhaseVoltage        | Voltage of phase A                                                          | V    | Numeric        |
| bPhaseVoltage        | Voltage of phase B                                                          | V    | Numeric        |
| cPhaseVoltage        | Voltage of phase C                                                          | V    | Numeric        |
| aPhaseCurrent        | Current of phase A                                                          | A    | Numeric        |
| bPhaseCurrent        | Current of phase B                                                          | A    | Numeric        |
| cPhaseCurrent        | Current of phase C                                                          | A    | Numeric        |
| powerFactor          | Power factor (cosφ)                                                         | -    | Numeric        |
| gridFrequency        | Grid frequency                                                              | Hz   | Numeric        |
| pvPower              | Total PV power currently generated                                          | kW   | Numeric        |
| pv1Voltage           | PV string 1 voltage                                                         | V    | Numeric        |
| pv1Current           | PV string 1 current                                                         | A    | Numeric        |
| pv2Voltage           | PV string 2 voltage                                                         | V    | Numeric        |
| pv2Current           | PV string 2 current                                                         | A    | Numeric        |
| pv3Voltage           | PV string 3 voltage                                                         | V    | Numeric        |
| pv3Current           | PV string 3 current                                                         | A    | Numeric        |
| pv4Voltage           | PV string 4 voltage                                                         | V    | Numeric        |
| pv4Current           | PV string 4 current                                                         | A    | Numeric        |
| internalTemperature  | Device internal temperature                                                 | ℃    | Numeric        |
| insulationResistance | Device insulation resistance                                                | MΩ   | Numeric        |
| pvEnergyDaily        | Daily PV energy generation                                                  | kWh  | Numeric/String |
| pvEnergyTotal        | Total PV energy generation (lifetime)                                       | kWh  | Numeric/String |
| batPower             | Battery real-time power (positive: discharging, negative: charging)         | kW   | Numeric        |
| pvTotalPower         | Combined PV output power                                                    | kW   | Numeric        |
| pcsReactivePower     | PCS (Power Conversion System) reactive power                                | kW   | Numeric        |
| pcsActivePower       | PCS (Power Conversion System) active power                                  | kW   | Numeric        |
| esDischargingDay     | Battery energy discharged today                                             | kWh  | Numeric        |
| esChargingDay        | Battery energy charged today                                                | kWh  | Numeric        |
| pvPowerDay           | Daily PV power                                                              | kWh  | Numeric        |
| esDischargingTotal   | Total battery discharged energy (lifetime)                                  | kWh  | Numeric        |
| batSoc               | Battery State of Charge                                                     | %    | Numeric        |

## Gateway Realtime Data
| Key                  | Description               | Unit | Data Type |
|-------------------|--------|---------------------------|-----------|
| voltageA             | Phase A voltage           | V    | Numeric   |
| voltageB             | Phase B voltage           | V    | Numeric   |
| voltageC             | Phase C voltage           | V    | Numeric   |
| currentA             | Phase A current           | A    | Numeric   |
| currentB             | Phase B current           | A    | Numeric   |
| currentC             | Phase C current           | A    | Numeric   |
| activePower          | Phase A apparent power    | kW   | Numeric   |
| reactivePower        | Phase A reactive power    | kW   | Numeric   |

## Meter Realtime Data
| Key                  | Description               | Unit | Data Type |
|-------------------|--------|---------------------------|-----------|
| voltageA             | Phase A voltage           | V    | Numeric   |
| voltageB             | Phase B voltage           | V    | Numeric   |
| voltageC             | Phase C voltage           | V    | Numeric   |
| currentA             | Phase A current           | A    | Numeric   |
| currentB             | Phase B current           | A    | Numeric   |
| currentC             | Phase C current           | A    | Numeric   |
| powerFactor          | Power factor              | -    | Numeric   |
| gridFrequency        | Grid frequency            | Hz   | Numeric   |
| activePower          | Total active power (consumption/generation) | kW | Numeric |
| reactivePower        | Total reactive power (consumption/generation) | kW | Numeric |

# Sample Request
```json
{
    "systemId" : "NDXZZ1731665796",
    "serialNumber" : "110B115K0053"
}
```

# Sample Successful Response
```json
{
  "code": 0,
  "msg": "success",
  "timestamp": 1757583276,
  "data": {
    "systemId": "NDXZZ1731665796",
    "serialNumber": "110B115K0053",
    "deviceType": "Inverter",
    "realTimeInfo": {
      "activePower": -0.115,
      "reactivePower": -0.005,
      "aPhaseVoltage": 230.75,
      "bPhaseVoltage": 235.62,
      "cPhaseVoltage": 233.66,
      "aPhaseCurrent": 0.76,
      "bPhaseCurrent": 0.81,
      "cPhaseCurrent": 0.73,
      "powerFactor": -0.996,
      "gridFrequency": 49.97,
      "pvPower": 0.0,
      "pv1Voltage": 0.0,
      "pv1Current": 0.0,
      "pv2Voltage": 0.0,
      "pv2Current": 0.0,
      "pv3Voltage": 0.0,
      "pv3Current": 0.0,
      "internalTemperature": 54.7,
      "insulationResistance": 0.685,
      "pvEnergyDaily": "0.00",
      "pvEnergyTotal": "1299.16",
      "pv4Voltage": -0.1,
      "pv4Current": -0.01,
      "batPower": -0.001,
      "pvTotalPower": 0.0,
      "pcsReactivePower": -0.005,
      "pcsActivePower": -0.115,
      "esDischargingDay": 0.01,
      "esChargingDay": 0.0,
      "pvPowerDay": 0.0,
      "esDischargingTotal": 1077.98,
      "batSoc": 39.0
    }
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