# Operating Mode Description

| Enumerations | Description |
| --- | --- |
| charge | Forced battery charging. |
| discharge | Forced battery discharge. |
| idle | Not charging and not discharging. |
| selfConsumption | Surplus PV power gives priority to charging. |
| selfConsumption-grid | Surplus PV power prioritizes grid injection. |

Please refer to [Detailed of Energy Storage Operating Modes](https://developer.sigencloud.com/user/api/document/65) for specific logic for each mode.

# Charge Priority Description

| Enumerations | Description |
| --- | --- |
| PV  | Prioritize charging from solar PV |
| GRID | Prioritize charging from grid |

# Discharge Priority Description

| Enumerations | Description |
| --- | --- |
| PV  | Prioritize discharging PV power |
| BATTERY | Prioritize discharging battery power |
