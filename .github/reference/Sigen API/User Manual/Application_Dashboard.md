# Overview

Developers can use the application dashboard to manage their apps. The dashboard currently provides:

- App settings (generate & view AppKey & AppSecret)
- API list
- Authentication
- data subscription

# Generate & view AppKey & AppSecret

When calling APIs, developers must pass the application credentials (AppKey & AppSecret) as parameters.

Go to **App settings** in the dashboard to view the app’s **AppKey**. The first time, click the **Generate** button to create the **AppSecret**. The generated **AppSecret** can be viewed only once—please store it securely.

# API list

Developers can view the list of APIs the application is authorized to use, each endpoint’s call-rate limits, and direct links to the corresponding API documentation.

![pic](https://s3.cn-northwest-1.amazonaws.com.cn/sigen-data-public/8632284413ee41169be92c2f0fae08c3.png)

# Authentication

Developers can view and search all devices under the application that have been authenticated via **onboard/password** methods, and review each device’s **Authentication** history.

![pic](https://s3.cn-northwest-1.amazonaws.com.cn/sigen-data-public/d747bcd192cd44439be6998753f7359c.png)

# data subscription

## Subscription mode settings

Developers can choose different **data subscription** modes to receive pushed data from Sigenergy devices. We recommend using **MQTT**. [Why MQTT?](https://developer.sigencloud.com/user/api/document/39)

![pic](https://s3.cn-northwest-1.amazonaws.com.cn/sigen-data-public/f66accc6de1a4dc79ec9534a19cea41e.png)


**MQTT:** In the data subscription module, developers can view MQTT subscription configurations, including:

* MQTT connection address
* System (site) data topic
* Telemetry data topic
* Alarm data topic
* MQTT security certificates (downloadable directly from the portal)

**http:** If you choose **http** as the subscription mode, fill in and save the following:

* System data endpoint URL
* Telemetry data endpoint URL
* Alarm data endpoint URL

## Subscription data settings

Developers can select which data items to subscribe to based on actual needs.

* **Enable/disable items:** Choose whether to receive a given data item.
* **Data alias settings:** Customize aliases for data items. You can use the modified alias as parameters for **data subscription**.

After saving the configuration, you still need to perform the subscription operation according to your settings. [How to subscribe?](https://developer.sigencloud.com/user/api/document/44)