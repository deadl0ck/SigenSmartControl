- The Sigenergy Openapi is equipped with powerful and flexible data transmission capabilities. It can regularly and stably push the telemetry data of system equipment to the service according to a preset time cycle. This function plays a crucial role in ensuring the efficient and intelligent operation of the power system. Currently, to meet the diverse needs of different users and application scenarios, we especially support customized settings for data push intervals and content.

- Users can determine the data push interval according to their actual business situations. In terms of data push content, a wide range of options are provided, covering the operating status data of equipment, such as equipment health status information, inverter power, grid connection point power, etc. This comprehensively helps users gain an in-depth understanding of the operating conditions of system equipment.

- If you have any questions regarding the customization of data push intervals and content during the usage process, or if you need further technical support and detailed information, please do not hesitate to contact our professional support team. The members of our team all possess profound professional knowledge and rich practical experience. We will serve you wholeheartedly to ensure that you can make full use of all the functions of the Sigenergy Openapi and achieve the best system operation results.

# Reminders
- The push interval is 5 minutes at default.
- The topics for telemetry data can be viewed under the "Data Subscription" section in the Control Center application of the Open Platform.
- Data reception requires using the subscription interface in [Subscription For Telemetry](https://developer.sigencloud.com/user/api/document/45).
- If the pushed data does not meet your requirements, please contact technician.

# Data Example
```json
[
    {
        "deviceType": "system",
        "systemId": "AXBNH1123789222",
        "snCode": "AXBNH1123789222",
        "statisticsTime": 1757407020,
        "value": {
            "gridPhaseCReactivePowerVar": "-51.0",
            "inverterActivePowerW": "4207.0",
            "inverterReactivePowerVar": "-6.0",
            "inverterPhaseBReactivePowerVar": "0.0",
            "inverterMaxChargePowerW": "4199.0",
            "inverterMaxAbsorptionActivePowerW": "5000.0",
            "inverterPhaseAActivePowerW": "4192.0",
            "inverterPhaseBActivePowerW": "0.0",
            "gridActivePowerW": "-4151.0",
            "inverterMaxFeedInActivePowerW": "5000.0",
            "inverterMaxDischargePowerW": "2466.0",
            "inverterPhaseAReactivePowerVar": "0.0",
            "inverterPhaseCActivePowerW": "0.0",
            "inverterMaxFeedInReactivePowerVar": "3000.0",
            "storageChargeCapacityWh": "6090.0",
            "storageDischargeCapacityWh": "1960.0",
            "pvPowerW": "1740.0",
            "gridPhaseBActivePowerW": "0.0",
            "gridPhaseBReactivePowerVar": "0.0",
            "gridPhaseAActivePowerW": "-4184.0",
            "storageChargeDischargePowerW": "-2464.0",
            "storageSOC%": "24.4",
            "inverterPhaseCReactivePowerVar": "0.0",
            "gridPhaseCActivePowerW": "33.0",
            "gridReactivePowerVar": "-51.0",
            "inverterMaxAbsorptionReactivePowerVar": "3000.0",
            "gridPhaseAReactivePowerVar": "0.0"
        }
    }
]
```

# Data Field Description
| Name          | Type   | Description               |
|---------------|--------|---------------------------|
| systemId      | String | The unique code representing the power station |
| snCode        | String | Serial Number code of system |
| deviceType    | String | Type of device: system    |
| statisticsTime| Long   | Time of statistics        |
| value         | String key-value | Telemetry data of interest |

> For detailed signal description, please refer to [Telemetry Signals Description](https://developer.sigencloud.com/user/api/document/63)
