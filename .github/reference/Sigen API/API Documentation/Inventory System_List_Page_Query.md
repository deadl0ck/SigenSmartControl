Used to query a list of stations that meet the specified criteria. Supports pagination and filtering by grid-connection time.

## Access Restrictions

* Each account can **access only once every 5 minutes**.
* Each page can return a maximum of **100 station records**.
* If only the grid connection start time (`startTime`) is provided, the grid connection end time (`endTime`) defaults to the current time.
* If only the grid connection end time (`endTime`) is provided, the grid connection start time (`startTime`) defaults to **January 1, 1970, 08:00:00**.

---

## Request Parameters

| Parameter | Type    | Required | Description                                                |
| --------- | ------- | -------- | ---------------------------------------------------------- |
| startTime | Long    | No       | Station grid connection start time, in seconds (timestamp) |
| endTime   | Long    | No       | Station grid connection end time, in seconds (timestamp)   |
| pageNum   | Integer | Yes      | Page number for paginated queries                          |
| pageSize  | Integer | No       | Number of records per page, defaults to 100                |

---

## Response Parameters

| Parameter          | Type         | Description                                                                 |
| ------------------ | ------------ | --------------------------------------------------------------------------- |
| code               | Integer      | Error code                                                                  |
| msg                | String       | Message                                                                     |
| data               | Object       | Returned data object, containing the following fields:                      |
| > total            | Long         | Total number of stations                                                    |
| > size             | Long         | Records per page                                                            |
| > current          | Long         | Current page number                                                         |
| > pages            | Long         | Total number of pages                                                       |
| > records          | List<String> | List of station data for the current page, containing the following fields: |
| >> systemId        | String       | System ID                                                                   |
| >> systemName      | String       | System name                                                                 |
| >> addr            | String       | System address                                                              |
| >> status          | String       | System operating status                                                     |
| >> isActivate      | Boolean      | Whether the system is activated                                             |
| >> onOffGridStatus | String       | On-grid / Off-grid status                                                   |
| >> timeZone        | String       | System time zone                                                            |
| >> gridConnectTime | Long         | Grid connection time (timestamp)                                            |
| >> pvCapacity      | Integer      | PV capacity                                                                 |
| >> batteryCapacity | Integer      | Battery capacity                                                            |

---

## Request Example

```json
{
    "startTime" : "1711468800",
    "endTime" : "1711814399",
    "pageNum" : 0,
    "pageSize" : 10
}
```

---

## Success Response Example

```json
{
  "code": 0,
  "msg": "success",
  "timestamp": 1761189111,
  "data": {
    "records": [
      "{\"systemId\":\"IXXMQ1743989898\",\"systemName\":\"L1\",\"addr\":\"Minhang District, Shanghai, China\",\"status\":\"Normal\",\"isActivate\":true,\"onOffGridStatus\":\"onGrid\",\"timeZone\":\"Asia/Shanghai\",\"gridConnectedTime\":1743665483000,\"pvCapacity\":5,\"batteryCapacity\":5}",
      "{\"systemId\":\"SGHGO1761898989\",\"systemName\":\"L2\",\"addr\":\"Minhang District, Shanghai, China\",\"status\":\"Disconnection\",\"isActivate\":true,\"onOffGridStatus\":\"onGrid\",\"timeZone\":\"Asia/Shanghai\",\"gridConnectedTime\":1761101752000,\"pvCapacity\":50,\"batteryCapacity\":50}"
    ],
    "total": 2,
    "size": 10,
    "current": 1,
    "pages": 1
  }
}
```

---

## Failure Response Example

```json
{
    "code": 1000,
    "msg": "param illegal",
    "data": null
}
```
