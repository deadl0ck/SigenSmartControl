* In intelligent power systems, for certain information that does not change frequently, such as battery capacity and operational status, Sigenergy Openapi provides this data in full during the system onboarding process. This allows vendors to have a clear understanding of the system’s basic configuration and initial operational status from the very beginning.

* Once these data change subsequently, Sigenergy Openapi will immediately push the latest updated data to the vendors. This is crucial. For example, in battery charge and discharge operations, knowing the maximum charge and discharge power accurately is essential to properly set the power parameters, ensuring battery lifespan as well as system stability and safety.

* Considering the diverse data needs of different users, Sigenergy Openapi supports customized data services. If you have any questions regarding data customization or the provision and update mechanism of the above information, and wish to learn more details, please contact our professional support team.

# Notes

* The push is triggered when the data changes.
* The topic for system data can be viewed under the "Data Subscription" section in the Control Center application of the Open Platform.
* Data reception requires using the [System Data Subscription Interface](https://developer.sigencloud.com/user/api/document/47).
* Data adjustment: if the pushed data does not meet your requirements, please contact the technical team.

# Data Example

```json
[
    {
        "deviceType":"system",
        "systemId":"KXGCS1727160960",
        "snCode":"KXGCS1727160960",
        "value": {
            "onOffGridStatus":"on",
            "inverterMaxActivePowerW":"2400",
            "inverterMaxApprentPowerVar":"2511",
            "systemStatus":"running",
            "batteryRatedChargePowerW":"2000",
            "batteryRatedDischargePowerW":"2000",
            "gridMaxBackfeedPowerW":"2000",
            "inverterMaxAbsorptionPowerW":"2000",
            "batteryRatedCapabilityWh":"4000"
        }
    }
]
```

# Data Field Description

| Name       | Type             | Description                      |
| ---------- | ---------------- | -------------------------------- |
| systemId   | String           | Unique code of the power station |
| snCode     | String           | Device serial number code        |
| deviceType | String           | Type of device                   |
| value      | String key-value | Telemetry data                   |

> For detailed signal description, please refer to [System Data Subscription Signals](https://developer.sigencloud.com/user/api/document/64)
