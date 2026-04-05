This API is used to send control commands to a specified energy storage system, enabling efficient management and scheduling of the system’s energy. Through this interface, users can flexibly control the charging and discharging states, power limits, and PV/grid priority strategies of the energy storage system to meet different operational scenarios. Typical applications include:

1. **Battery Operation Mode Control**: Set the system to charge, discharge, self-consumption, or idle modes to adapt to various energy management strategies.
2. **Power Limit Configuration**: Set charging power, PV charging power, maximum export power, and maximum import power to finely control the energy inflow and outflow of the system.
3. **Priority Strategy Management**: Configure the source priority for charging and discharging (PV or grid/battery), supporting flexible scheduling and optimized energy utilization.
4. **Time-Slot Control**: Specify the start time and duration of commands to achieve scheduled operations and batch dispatch.
5. **Batch Command Issuance**: For each site, a maximum of 24 instructions can be received in each batch.

# Request Style
| Parameter         | Type    | Required | Description                                        |
| ----------------- | ------- | -------- | -------------------------------------------------- |
| accessToken       | String  | Yes      | Authorization token obtained from Chapter 2        |
| systemId          | String  | Yes      | Unique code of the power station                   |
| activeMode        | String  | Yes      | System active mode                                 |
| startTime         | Long    | Yes      | Command start time, in seconds                     |
| duration          | Integer | Yes      | Command duration, in minutes                       |
| chargingPower     | Double  | No       | Max energy storage charging/discharging power (KW) |
| pvPower           | Double  | No       | Max photovoltaic charging power (KW)               |
| maxSellPower      | Double  | No       | Max export power to the grid (KW)                  |
| maxPurchasePower  | Double  | No       | Max purchase power from the grid (KW)              |
| chargePriorityType    | Enum    | No       | Charging priority (PV/GRID)                        |
| dischargePriorityType | Enum    | No       | Discharging priority (PV/BATTERY)                  |


# Sample Request

## Basic Example
```json
{
    "accessToken": " JFf_QTaUkGTD9AQXiMrfLBUfM3v2qyLPr3KOole ",
    "commands": [{
        "systemId": "KXGCS1727160960",
        "activeMode": "charge",
        "startTime": 1715154185,
        "duration": 2,
        "chargingPower": 3.2,
        "pvPower": 1.8
    }]
}
```

## Scenario 1: No PV power curtailment, battery charged by PV
```json
{
    "systemId": "FAZGW8745476782",
    "activeMode": "selfConsumption",
    "startTime": 1691572800000,
    "duration": 30
}
```

## Scenario 2: PV power curtailed, battery charged by grid first
```json
{
    "systemId": "FAZGW8745476782",
    "activeMode": "charge",
    "startTime": 1691572800000,
    "duration": 30,
    "chargingPower": 25.0,
    "chargePriorityType": "GRID"
}
```

## Scenario 3: PV curtailed, battery idle, grid does not supply power
```json
{
    "systemId": "FAZGW8745476782",
    "activeMode": "idle",
    "startTime": 1691572800000,
    "duration": 30,
    "maxSellPower": 0
}
```
