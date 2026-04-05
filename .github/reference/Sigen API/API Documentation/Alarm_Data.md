# Reminders

- Relevant data will be pushed when alarm generation.
- Data reception requires using the [Alarm Subscription](https://developer.sigencloud.com/user/api/document/49)

# Data Example
```json
[
    {
        "systemId": "KXGCS1727160960",
        "alarmCode": "1001",
        "status": "generation",
        "changeTime": 1716173149647
    }
]
```

# Data Field Description
| Name | Type | Description |
| --- | --- | --- |
| systemId | String | System ID. |
| alarmCode | Integer | Code of the alarm. |
| status | String | Status for alarm: generation/recovery. |
| changeTime | Long | Time of event occurrence. |
