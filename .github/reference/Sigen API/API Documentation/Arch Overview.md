# System Architecture

Currently, the interaction between third-party systems and the **OpenAPI Service** is mainly implemented through **HTTP protocol** and **MQTT protocol**:

![biz1](https://sigen-data-cn-public.s3.cn-northwest-1.amazonaws.com.cn//biz11.jpg)

* **HTTP Protocol**: Suitable for **synchronous request scenarios**, such as **authentication**, **user registration**, and **data query** modules. The caller can obtain the execution result immediately after the request is completed.
* **MQTT Protocol**: Suitable for **asynchronous message scenarios**, such as **data push** and **command control** modules. The OpenAPI service can also proactively push data to third-party systems via the MQTT protocol.

![biz2](https://sigen-data-cn-public.s3.cn-northwest-1.amazonaws.com.cn//biz22.jpg)

# Rate Limiting Policy

To ensure the rational use of system resources, improve overall service efficiency, and effectively defend against external malicious traffic attacks, the OpenAPI Service defines rate limiting rules based on the expected access scale.

For requests beyond the expected range, the system may take measures such as **service rejection**, **request queuing**, or **service degradation**.

The current rate limiting policies are as follows:

1. **Device Quantity Limit per Request**

   * The number of devices involved in a single API request must be fewer than **200 units**.

2. **API Access Frequency Limit**

   * Depending on the business characteristics and resource consumption, the platform sets differentiated frequency limits for different APIs. Detailed rules are specified in the corresponding API documentation.
   * If you need to adjust the access frequency limit, please contact the technical support team.

# General Notes

1. **Domain**

   * All API requests must use the specified domain: `openapi-eu.sigencloud.com`.

2. **Topic**

   * The OpenAPI Service allocates dedicated **Topics** for third-party systems based on business types and access configurations.
   * The specific Topic can be viewed in the **Data Subscription** section of the application in the Open Platform Console.
   * Topics for receiving commands and configuration have been clearly defined in the corresponding documents. Please refer to:

     * [Subscription](https://developer.sigencloud.com/user/api/document/44)
     * [Instruction](https://developer.sigencloud.com/user/api/document/55)

3. **Request**

   * All API interfaces are based on the **HTTP protocol**.
   * The request header must include the **Authorization** parameter for permission validation.
   * Request parameters must follow the examples provided in the API documentation.

4. **Response Body**

   * All responses are in **standard JSON format** and can be parsed using common JSON libraries.
   * For HTTP responses, the response header **Content-Type** must be set to `application/json`.

   **Response Body Field Description**:

   | Name      | Type    | Description                  |
   | --------- | ------- | ---------------------------- |
   | code      | Integer | Status code                  |
   | msg       | String  | Description of the status    |
   | timestamp | Long    | Response generation time (s) |
   | data      | Object  | Data content returned by API |
