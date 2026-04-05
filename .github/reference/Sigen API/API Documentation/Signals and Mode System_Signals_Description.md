| Name | Description |
| --- | --- |
| onOffGridStatus | The system is on grid or off grid. |
| inverterMaxActivePowerW | This is should be the base value of all active power adjustment actions. |
| inverterMaxApprentPowerVar | This is should be the base value of all reactive power adjustment actions. |
| systemStatus | This should be the status of the system. Enumeration: standby running fault shutdown disconnected |
| batteryRatedChargePowerW | Rated energy storage charging power is the maximum power a battery can safely receive during charging. |
| batteryRatedDischargePowerW | Rated energy storage discharging power is the maximum power a battery can safely deliver during discharging. |
| gridMaxBackfeedPowerW | Permissible feed in power at the grid connection point is the maximum power that is allowed to be sent from the grid into a local system. |
| batteryRatedCapabilityWh | The total rated capacity of system refers to the maximum amount of electrical energy the entire system can generate or store under specific operating conditions. |
| inverterMaxAbsorptionActivePowerW | The highest active power that can be absorbed from the grid by a system.
| chargeCutOffSOC%      | Charge cut-off SOC. When battery SOC ≥ this threshold, the system stops charging. Range: 0–100.                                                                                                           |
| dischargeCutOffSOC%   | Discharge cut-off SOC. When battery SOC ≤ this threshold, the system stops discharging. Range: 0–100.                                                                                                     |
| backupCutOffSOC%      | Backup cut-off SOC. In backup mode, when battery SOC ≤ this threshold, discharging is limited or stopped. Range: 0–100.                                                                                   |
| peakShavingCutOffSOC% | Peak shaving cut-off SOC. Effective only when peakShavingStatus ≠ 0; when battery SOC ≤ this threshold, peak shaving discharge is stopped. Range: 0–100.                                                  |
| peakShavingStatus     | Peak shaving status. Indicates whether the system is in peak shaving control mode and affects charge/discharge behavior. Enumeration: off = Disabled; on = enabled |
| stormWatchStatus      | Storm Watch status. Indicates whether the system is in Storm Watch mode and affects charge/discharge behavior. Enumeration: off = Disabled; on = enabled |