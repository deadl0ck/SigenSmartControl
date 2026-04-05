# Site Onboarding Permissions & Flow Differences

> This chapter explains how using (or not using) an **invitation code** during developer registration results in different **onboarding permissions and flows** when onboarding a customer site.

---

## Terms & Roles

- **Developer**: An organization/individual registered on the Developer Platform and owning an app
- **Owner**: The site owner/admin (typically the primary email of the site account)
- **Onboard / Onboarding**: The process of authorizing a customer site to connect to the developer’s app/service
- **Invitation Code**: A code that grants an advanced onboarding permission level

---

## Invitation Code Overview

Invitation codes are issued by **Sigenergy**. Sigenergy proactively invites qualified developers and provides an invitation code. **We do not recommend proactively asking whether you qualify**; if eligible, you will typically receive an official invitation and instructions.

> Invitation codes are usually used during **registration/verification** to attach advanced permissions to the developer organization or account.

---

## Onboarding Permissions & Flows

There are two onboarding permission levels: **Standard** and **Advanced**. The key difference is whether **Owner Consent** (email confirmation) is required.

---

### Standard (No Invitation Code)

**Capability**  
Developers can initiate an onboard request, but it becomes effective only after the owner confirms.

**Flow**  
1) Developer creates an onboard request  
2) Owner receives a confirmation email  
3) Owner clicks “Accept/Confirm”  
4) Onboard succeeds

![图片](https://s3.cn-northwest-1.amazonaws.com.cn/sigen-data-public/880ecc2d19f74fbbaf0e84db227993b9.png)

**Implementation Tips**

* Clearly show: “Owner email confirmation is required to complete onboarding.”
* If supported, provide “Resend email / Check pending status”.

---

### Advanced (With Invitation Code)

**Capability**
Developers with an invitation code are granted **advanced onboarding permission**: once platform validation passes, onboarding completes **immediately** without owner email confirmation.

**Typical Flow**

1. Developer uses the invitation code during registration/verification.
2. Developer initiates an onboard request for a target site.
3. Platform validates permission & site eligibility.
4. Onboarding succeeds instantly and access is granted.

**Notes**

* Advanced permission does not mean “unrestricted onboarding”; the platform may enforce policies (region/customer type/product line/quotas, etc.).
* In your UI, clearly indicate that onboarding will complete immediately, and consider an extra confirmation step to prevent mistakes.
* Keep audit records: actor, timestamp, app, target site, result.