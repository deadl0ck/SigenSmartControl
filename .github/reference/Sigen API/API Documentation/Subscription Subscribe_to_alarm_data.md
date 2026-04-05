# Request Parameter
| Name       | Type       | Required | Description                     |
|----------------|----------------|--------------|--------------------------------------|
| accessToken    | String         | Yes          | The authorization code generated according to Chapter 2. |
| systemIdList   | List<String>   | Yes          | System unique code list             |

# Sample Request
```json
{
    "accessToken": "4JTOb5E5WBtzM7OPWiRZITKzN45URcq1",
    "systemIdList": ["CBFGA1627168228"]
}
```

# Reminders
- Data interaction is carried out in JSON format.
- The api requires authorization in northbound scene.
