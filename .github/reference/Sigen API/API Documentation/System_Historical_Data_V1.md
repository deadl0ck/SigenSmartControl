# Request Parameters

| Field    | Type   | Required | Description                                                                                            |
| -------- | ------ | -------- | ------------------------------------------------------------------------------------------------------ |
| systemId | String | Yes      | Unique system identifier                                                      |
| date     | String | Yes      | Query date in the format: `YYYY-MM-DD`                                                                 |
| level    | String | Yes      | Time dimension (Day = daily, Week = weekly, Month = monthly, Year = yearly, Lifetime = full lifecycle) |

# Request Example

```json
"systemId": "NDXZZ1731665796",
"date": "2025-08-06",
"level": "Day"
```

# Response Example

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "sankeyData": {
      "nodes": [
        {
          "id": "FROM_SOLAR",
          "value": 66.03
        },
        {
          "id": "FROM_BATTERY",
          "value": 0
        },
        {
          "id": "TO_BATTERY",
          "value": 3.88
        },
        {
          "id": "TO_EVDC",
          "value": 0
        },
        {
          "id": "FROM_EVDC",
          "value": 0.11
        },
        {
          "id": "FROM_GRID",
          "value": 1.11
        },
        {
          "id": "TO_LOAD",
          "value": 1.21
        },
        {
          "id": "TO_GRID",
          "value": 62.16
        }
      ],
      "links": [
        {
          "sourceId": "FROM_SOLAR",
          "targetId": "TO_BATTERY",
          "value": 3.87
        },
        {
          "sourceId": "FROM_SOLAR",
          "targetId": "TO_GRID",
          "value": 62.16
        },
        {
          "sourceId": "FROM_EVDC",
          "targetId": "TO_BATTERY",
          "value": 0.01
        },
        {
          "sourceId": "FROM_EVDC",
          "targetId": "TO_LOAD",
          "value": 0.1
        },
        {
          "sourceId": "FROM_GRID",
          "targetId": "TO_LOAD",
          "value": 1.11
        }
      ]
    },
    "chartData": {
      "chartName": "Energy",
      "unit": "kW",
      "chartType": "LINE",
      "dataSeries": [
        {
          "id": "FROM_SOLAR",
          "points": [
            {
              "time": "20250806 00:00",
              "value": 4.79
            },
            {
              "time": "20250806 14:20",
              "value": 4.69
            }
          ]
        },
        {
          "id": "BATTERY",
          "points": [
            {
              "time": "20250806 00:00",
              "value": 0
            },
            {
              "time": "20250806 14:20",
              "value": -0.9
            }
          ]
        },
        {
          "id": "TO_EVDC",
          "points": [
            {
              "time": "20250806 00:00",
              "value": 0
            },
            {
              "time": "20250806 14:20",
              "value": 0
            }
          ]
        },
        {
          "id": "FROM_EVDC",
          "points": [
            {
              "time": "20250806 00:00",
              "value": 0
            },
            {
              "time": "20250806 14:20",
              "value": 0
            }
          ]
        },
        {
          "id": "GRID",
          "points": [
            {
              "time": "20250806 00:00",
              "value": -4.79
            },
            {
              "time": "20250806 14:20",
              "value": -3.79
            }
          ]
        },
        {
          "id": "TO_LOAD",
          "points": [
            {
              "time": "20250806 00:00",
              "value": 0
            },
            {
              "time": "20250806 14:20",
              "value": -0.01
            }
          ]
        }
      ],
      "preTimePeriodTotal": null
    }
  }
}
```

# Data Interpretation

## Sankey Diagram Usage
The Sankey diagram visualizes energy flow through the power station system:
- **Nodes** represent energy sources, storage, and consumption points
- **Links** show the flow of energy between components
- **Values** indicate the amount of energy transferred

## Chart Data Usage
The chart data provides time-series information for detailed analysis:
- **Positive values** typically represent energy generation or import
- **Negative values** typically represent energy consumption or export
- **Time points** are provided at regular intervals based on the selected time dimension

## Value Interpretation
- **FROM_SOLAR**: Solar energy generation (always positive)
- **BATTERY**: Positive = discharging, Negative = charging
- **GRID**: Positive = importing from grid, Negative = exporting to grid
- **TO_LOAD**: Energy consumption by loads (usually positive or zero)
- **TO_EVDC/FROM_EVDC**: EV charger energy flow

# Energy Component IDs

## Source Components (Energy Generation)
- `FROM_SOLAR` - Solar panel energy generation
- `FROM_BATTERY` - Energy discharged from battery
- `FROM_EVDC` - Energy from EV DC charger
- `FROM_GRID` - Energy imported from grid

## Target Components (Energy Consumption)
- `TO_BATTERY` - Energy charged to battery
- `TO_EVDC` - Energy sent to EV DC charger
- `TO_LOAD` - Energy consumed by loads
- `TO_GRID` - Energy exported to grid

## Bidirectional Components
- `BATTERY` - Battery charge/discharge (positive = discharge, negative = charge)
- `GRID` - Grid import/export (positive = import, negative = export)


# Response Structure

## C0

| Field      | Type | Description                                                                                |
| ---------- | ---- | ------------------------------------------------------------------------------------------ |
| sankeyData | C1   | Sankey diagram data for visualizing cumulative energy flows within the selected time frame |
| chartData  | C2   | Time-series energy chart data, historical values at each sampling point                    |

---

## C1: Sankey Data

| Field | Type        | Description                                       |
| ----- | ----------- | ------------------------------------------------- |
| nodes | List\<C1-1> | Node list representing energy system components   |
| links | List\<C1-2> | Link list representing energy flows between nodes |

### C1-1: Node

| Field | Type   | Description                                      |
| ----- | ------ | ------------------------------------------------ |
| id    | String | Unique identifier of the energy system component |
| value | Double | Total energy associated with this component      |

### C1-2: Link

| Field    | Type   | Description                          |
| -------- | ------ | ------------------------------------ |
| sourceId | String | Identifier of the source node        |
| targetId | String | Identifier of the target node        |
| value    | Double | Energy flow amount between the nodes |

---

## C2: Chart Data

| Field              | Type        | Description                               |
| ------------------ | ----------- | ----------------------------------------- |
| chartName          | String      | Chart display name                        |
| unit               | String      | Data unit (e.g., `kW`, `kWh`)             |
| chartType          | String      | Chart type (e.g., `LINE`, `BAR`)          |
| dataSeries         | List\<C2-1> | Collection of data series                 |
| preTimePeriodTotal | Double      | Total from the previous period (nullable) |

### C2-1: Data Series

| Field  | Type      | Description               |
| ------ | --------- | ------------------------- |
| id     | String    | Data series identifier    |
| points | List\<C3> | Collection of data points |

### C3: Data Point

| Field | Type   | Description                          |
| ----- | ------ | ------------------------------------ |
| time  | String | Timestamp in `YYYYMMDD HH:mm` format |
| value | Double | Energy value at the given timestamp  |

# Notes

1. **Data Consistency**:

   * Sankey data shows **cumulative totals** for the selected period
   * Chart data provides **time-series values**

2. **Energy Balance**:

   * Total energy input ≈ total output + storage variation

