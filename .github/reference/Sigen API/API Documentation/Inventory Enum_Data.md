# System Status Enumeration

| status  | Description                                          |
| ------- | ---------------------------------------------------- |
| Standby | No power is activated.                               |
| Normal  | All devices in the system are operating normally.    |
| Fault   | At least one device in the system is malfunctioning. |
| Offline | Device communication with the cloud is interrupted.  |

# System Type Enumeration

| type        | Description                               |
| ----------- | ----------------------------------------- |
| Residential | Residential solar system.                 |
| Commercial  | Commercial and industrial rooftop system. |

# Network Connection Type Enumeration

| type | Description                                                       |
| ---- | ----------------------------------------------------------------- |
| WIFI | Wireless network for local/internet access.                       |
| 4G   | Mobile broadband internet connection.                             |
| FE   | Wired connection via fiber Ethernet for high-speed data transfer. |

# Device Type Enumeration

| Device type | Description                                                                                                                 |
| ----------- | --------------------------------------------------------------------------------------------------------------------------- |
| Inverter    | Converts DC from solar panels into AC for home use and grid connection.                                                     |
| Battery     | Energy storage system that stores excess solar energy for use when sunlight is insufficient.                                |
| Gateway     | Central communication device that connects PV system components to the monitoring platform for remote control and analysis. |
| DcCharger   | Charges batteries directly using PV DC power, optimizing the charging process.                                              |
| AcCharger   | Charges electric vehicles or storage devices using grid AC or PV-converted AC.                                              |
| Meter       | Measures PV system generation, consumption, and grid feed-in.                                                               |

# Inverter Status Enumeration

| Device Status | Description                                          |
| ------------- | ---------------------------------------------------- |
| Standby       | No power is activated.                               |
| Normal        | Inverter is operating normally.                      |
| Fault         | At least one device in the system is malfunctioning. |
| Shutdown      | Inverter is shut down.                               |
| Offline       | Inverter is offline (communication interrupted).     |

# Battery Status Enumeration

| Device Status | Description                                     |
| ------------- | ----------------------------------------------- |
| Standby       | Battery is in standby mode.                     |
| Normal        | Battery is charging or discharging.             |
| Fault         | Battery system fault.                           |
| Dormancy      | Battery is not activated.                       |
| Offline       | Battery is offline (communication interrupted). |

# DC Charger Status Enumeration

| Device Status    | Description                                          |
| ---------------- | ---------------------------------------------------- |
| Init             | No power is activated.                               |
| Idle             | DC charger is idle.                                  |
| Normal           | All devices in the system are operating normally.    |
| Fault            | At least one device in the system is malfunctioning. |
| Shutdown         | DC charger is shut down.                             |
| Reset            | DC charger is resetting.                             |
| EmergencyStopped | DC charger is in emergency stop.                     |
| Offline          | DC charger is offline (communication interrupted).   |

# AC Charger Status Enumeration

| Device Status            | Description                                            |
| ------------------------ | ------------------------------------------------------ |
| IdleUnplugged            | AC charger is idle (plug not inserted).                |
| OccupiedNotStarted       | AC charger is occupied but not charging.               |
| PreparingWaitingCarStart | AC charger is ready, waiting for vehicle start signal. |
| Charging                 | AC charger is charging.                                |
| Fault                    | AC charger fault.                                      |
| Scheduled                | AC charger is scheduled.                               |
| Offline                  | AC charger is offline (communication interrupted).     |

# Gateway Status Enumeration

| Device Status | Description                                     |
| ------------- | ----------------------------------------------- |
| Normal        | Gateway is operating normally.                  |
| Fault         | Gateway fault.                                  |
| Offline       | Gateway is offline (communication interrupted). |

# Meter Status Enumeration

| Device Status | Description                                   |
| ------------- | --------------------------------------------- |
| Normal        | Meter is operating normally.                  |
| Offline       | Meter is offline (communication interrupted). |
