# AWS deployment guide

This deployment keeps the browser-facing application on one HTTPS origin while
storing all new profile photos, chat images, and chat videos privately in S3.
Authorized media requests are checked by FastAPI and redirected to a short-lived
CloudFront signed URL.

```text
Browser
  -> Route 53 / DNS
  -> Application Load Balancer (HTTPS + WSS, ACM certificate)
  -> one EC2 instance (FastAPI/Uvicorn, HTTP port 8000 inside the VPC)
       -> private RDS for PostgreSQL (TLS)
       -> private S3 bucket (IAM role)
FastAPI-authorized media request -> signed CloudFront URL -> S3 through OAC
```

Use one EC2 instance and exactly one Uvicorn worker for this version. WebSocket
routing and presence are held in process memory. Before adding more workers or
instances, add Redis (or another shared pub/sub/presence service).

## Values to decide first

The examples use Mumbai. Replace every angle-bracket placeholder before using a
sample file or command.

| Value | Example |
| --- | --- |
| AWS Region | `ap-south-1` |
| Application hostname | `chat.example.com` |
| Database name | `lan_chat` |
| Database owner username | `lan_chat_app` |
| S3 bucket | `my-company-lan-chat-media-123456789012` |
| EC2 application directory | `/opt/lan-chat` |

Keep all resources except CloudFront in the same Region. If you later assign a
custom hostname to CloudFront, its ACM certificate must be requested in
`us-east-1`; this guide uses the supplied `dxxxxx.cloudfront.net` hostname, so a
second certificate is unnecessary.

## 1. VPC and security groups

The default VPC is acceptable for a first deployment. A stricter production VPC
uses public subnets only for the load balancer, private subnets for EC2 and RDS,
and a NAT gateway or VPC endpoints for outbound access. The lower-cost setup in
this guide puts EC2 in a public subnet but exposes **no EC2 port to the Internet**:
only the load balancer security group can reach port 8000, and administration is
through Systems Manager Session Manager rather than SSH.

Create these security groups in the same VPC. It is easiest to create all three
first and add their rules afterward.

### `lan-chat-alb-sg`

- Inbound TCP 80 from `0.0.0.0/0` (redirects to HTTPS).
- Inbound TCP 443 from `0.0.0.0/0`.
- If the VPC and DNS use IPv6, add `::/0` for 80 and 443.
- Outbound custom TCP 8000 to security group `lan-chat-ec2-sg`.

### `lan-chat-ec2-sg`

- Inbound custom TCP 8000 with source `lan-chat-alb-sg` only.
- Do not add inbound 22, 80, 443, or any public CIDR.
- Leave the default outbound rule while setting up. It is needed for system
  packages, AWS APIs, S3, Secrets Manager, and CloudFront-related calls. Tighten
  it later with VPC endpoints and carefully scoped egress if required.

### `lan-chat-rds-sg`

- Inbound PostgreSQL TCP 5432 with source `lan-chat-ec2-sg` only.
- No public CIDR and no other inbound rule.

## 2. Create private RDS PostgreSQL

Open **RDS -> Databases -> Create database** and select:

- Creation method: **Standard create**.
- Engine: **PostgreSQL**.
- Engine version: the latest available PostgreSQL **17.x** minor release.
- Template: **Production** for durable data. For a low-cost prototype, choose
  **Dev/Test**, but retain every security selection below.
- Availability: **Multi-AZ DB instance** for real production; **Single DB
  instance** is cheaper for a prototype and has downtime during some failures.
- DB instance identifier: `lan-chat-postgres`.
- Master username: `lan_chat_app`.
- Credentials management: **Managed in AWS Secrets Manager**. Do not put the
  generated password in a file or EC2 environment variable.
- Instance class: `db.t4g.small` is a sensible small starting point. A
  `db.t4g.micro` is cheaper for very light testing.
- Storage: **General Purpose SSD (gp3)**, 20 GiB initially, storage autoscaling
  enabled with a maximum appropriate for your budget (for example 100 GiB).
- Storage encryption: **Enabled**. The AWS-managed RDS key is sufficient unless
  your policy requires a customer-managed KMS key.
- Connectivity: select the application VPC and an RDS DB subnet group covering
  at least two Availability Zones.
- Public access: **No**.
- VPC security group: choose existing `lan-chat-rds-sg`; remove `default`.
- Port: `5432`.
- Initial database name: `lan_chat`.
- Automated backups: enabled, 7 days or longer; choose a backup window.
- Encrypt Performance Insights if you enable it. Be aware of its cost.
- Enhanced Monitoring: optional; 60-second granularity is adequate initially.
- Auto minor version upgrade: enabled.
- Deletion protection: enabled.

For PostgreSQL 15 and newer, RDS defaults `rds.force_ssl` to `1`. Verify it on the
DB parameter group. This application additionally connects with
`sslmode=verify-full` and the AWS RDS CA bundle.

Wait until the instance is **Available**. Open its **Configuration** tab and copy:

- The RDS endpoint (do not include `:5432`).
- The ARN of the RDS-managed master secret.

The app is the only service using this database, so its managed database owner is
used for table creation and migrations. If the database is later shared, create a
separate application role limited to the `lan_chat` database/schema and replace
the secret with that role's secret.

## 3. Create the private S3 media bucket

Open **S3 -> Create bucket**:

- Bucket type: **General purpose**.
- Region: the same Region as EC2.
- Object Ownership: **Bucket owner enforced (ACLs disabled)**.
- Block Public Access: leave **all four settings enabled**.
- Bucket versioning: **Enable**.
- Default encryption: **SSE-S3** is supported with no additional key policy. You
  may select SSE-KMS, but then set `S3_KMS_KEY_ID` and grant the EC2 role the
  necessary KMS Encrypt/Decrypt/GenerateDataKey permissions.
- Object Lock: off unless you specifically need immutable retention.

Do not add S3 CORS rules and do not turn on S3 static website hosting. Browsers do
not upload directly to this bucket.

Recommended lifecycle rules:

- Abort incomplete multipart uploads after 7 days.
- Expire noncurrent versions after a retention period that matches your recovery
  policy, such as 30-90 days. Versioning otherwise retains deleted media and can
  increase storage cost.

## 4. Create CloudFront private delivery

### Generate the signing key

Generate a dedicated RSA-2048 key pair on a trusted computer (or a short-lived
CloudShell session), never on a public website:

```bash
openssl genrsa -out cloudfront-private-key.pem 2048
openssl rsa -pubout -in cloudfront-private-key.pem -out cloudfront-public-key.pem
```

In **CloudFront -> Public keys**, create a public key and paste the complete
contents of `cloudfront-public-key.pem`. Copy the resulting **public key ID**.
Then go to **Key groups**, create `lan-chat-media-signers`, add that public key,
and save.

In **Secrets Manager -> Store a new secret**:

- Secret type: **Other type of secret**.
- Choose the **Plaintext** editor and paste only the complete private PEM,
  including its BEGIN/END lines. A JSON object with a `private_key` string is also
  accepted, but raw PEM is simpler.
- Encryption key: `aws/secretsmanager` unless you require a customer-managed key.
- Secret name: `lan-chat/production/cloudfront-private-key`.
- Rotation: off; rotate this signing key deliberately by adding a new public key
  to the key group, deploying its private key/ID, and then retiring the old key.

Copy the secret ARN. Securely retain or destroy the local private-key file; never
commit it or copy it to EC2.

### Create the distribution

Open **CloudFront -> Create distribution**:

- Origin domain: select the S3 bucket's regular REST origin. Do not use an S3
  website endpoint.
- Origin access: **Origin access control settings (recommended)**.
- Create an OAC using **Sign requests: Always**.
- Viewer protocol policy: **Redirect HTTP to HTTPS**.
- Allowed methods: **GET, HEAD**.
- Cache policy: **CachingOptimized**.
- Compress objects automatically: **Yes**.
- Restrict viewer access: **Yes**.
- Trusted authorization type: **Trusted key groups**.
- Trusted key group: `lan-chat-media-signers`.
- Web Application Firewall: optional for this media-only distribution.
- Alternate domain name: leave blank for this guide.

After creation, copy the distribution ID and the domain such as
`d111111abcdef8.cloudfront.net`. Update the S3 bucket policy using
`deploy/s3-cloudfront-bucket-policy.json.example`: replace its three placeholders
and paste the result into **S3 -> bucket -> Permissions -> Bucket policy**.

The bucket remains private. A bare CloudFront media URL should return 403; only a
short-lived URL signed by the application should work.

## 5. Create the EC2 IAM role

In **IAM -> Policies -> Create policy -> JSON**, paste
`deploy/ec2-iam-policy.json.example` after replacing:

- `<DATABASE_SECRET_ARN>` with the RDS-managed secret ARN.
- `<CLOUDFRONT_PRIVATE_KEY_SECRET_ARN>` with the signing-secret ARN.
- `<BUCKET_NAME>` with the exact S3 bucket name.

Name it `LanChatApplicationPolicy`.

Create an IAM role:

- Trusted entity: **AWS service**.
- Use case: **EC2**.
- Attach `AmazonSSMManagedInstanceCore`.
- Attach `LanChatApplicationPolicy`.
- Name: `LanChatEc2Role`.

If either secret uses a customer-managed KMS key, also allow `kms:Decrypt` for
that exact key. Do not create an IAM user, access key, or `.aws/credentials` file.

## 6. Launch the Amazon Linux EC2 instance

Open **EC2 -> Launch instance**:

- Name: `lan-chat-app-1`.
- AMI: latest **Amazon Linux 2023** x86_64 AMI.
- Instance type: `t3.small` (2 GiB RAM) for a practical starting point.
- Key pair: **Proceed without a key pair** if Session Manager is your only admin
  path. Otherwise protect the key carefully; port 22 still does not need to be
  opened.
- VPC: the same VPC as RDS and the load balancer.
- Subnet: a public subnet for this lower-cost design.
- Auto-assign public IP: enabled for outbound package installation. The security
  group still prevents inbound Internet access.
- Firewall/security group: existing `lan-chat-ec2-sg` only.
- Storage: 20 GiB gp3, encrypted, delete on termination.
- Advanced details -> IAM instance profile: `LanChatEc2Role`.
- Metadata version: **V2 only (token required)**.
- Metadata response hop limit: `1`.
- Shutdown behavior: Stop.
- Termination protection: Enable for production.
- Detailed CloudWatch monitoring: optional and billable.

Launch it and wait for both status checks. Then use **Connect -> Session Manager ->
Connect**. If Session Manager is not ready, confirm the IAM role, outbound HTTPS,
and SSM Agent status rather than opening SSH to the world.

## 7. Install and configure the application on EC2

Run these commands in Session Manager. Replace the repository URL. For a private
Git repository, use a read-only deploy key or another short-lived credential; do
not place a personal token in the clone URL.

```bash
sudo dnf update -y
sudo dnf install -y git openssl python3.12 python3.12-pip postgresql17 nodejs20

sudo useradd --system --home-dir /opt/lan-chat --shell /sbin/nologin lan-chat
DEPLOY_USER="$(id -un)"
sudo mkdir -p /opt/lan-chat
sudo chown "${DEPLOY_USER}:${DEPLOY_USER}" /opt/lan-chat

git clone <YOUR_REPOSITORY_URL> /opt/lan-chat
cd /opt/lan-chat

python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

npm ci
npm run build

sudo chown -R "${DEPLOY_USER}:lan-chat" /opt/lan-chat
sudo chmod -R o-rwx /opt/lan-chat
```

Download and install the RDS CA bundle:

```bash
sudo curl --fail --show-error --location \
  https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem \
  --output /etc/pki/ca-trust/source/anchors/global-bundle.pem
sudo chmod 0644 /etc/pki/ca-trust/source/anchors/global-bundle.pem
```

Install the environment file and service:

```bash
sudo mkdir -p /etc/lan-chat
sudo cp /opt/lan-chat/deploy/lan-chat.env.example /etc/lan-chat/lan-chat.env
sudo chown root:lan-chat /etc/lan-chat /etc/lan-chat/lan-chat.env
sudo chmod 0750 /etc/lan-chat
sudo chmod 0640 /etc/lan-chat/lan-chat.env
sudo vi /etc/lan-chat/lan-chat.env
```

In the editor replace:

- `AWS_REGION`.
- `DATABASE_SECRET_ID` with the full RDS-managed secret ARN.
- `S3_BUCKET`.
- `CLOUDFRONT_DOMAIN` without `https://`.
- `CLOUDFRONT_PUBLIC_KEY_ID` with the CloudFront **public key ID**, not the key
  group ID or distribution ID.
- `CLOUDFRONT_PRIVATE_KEY_SECRET_ID` with the secret name or ARN.
- `ALLOWED_HOSTS` with the final application hostname only.
- `ALLOWED_ORIGINS` with the exact `https://` origin and no trailing slash.

There should be no database password, AWS access key, or private PEM in this file.

Install and start the systemd unit:

```bash
sudo cp /opt/lan-chat/deploy/lan-chat.service /etc/systemd/system/lan-chat.service
sudo systemctl daemon-reload
sudo systemctl enable --now lan-chat
sudo systemctl status lan-chat --no-pager
sudo journalctl -u lan-chat -n 100 --no-pager
curl --fail http://127.0.0.1:8000/healthz
```

Startup deliberately fails if production HTTPS, host/origin, database secret, S3,
or CloudFront signing settings are missing. A database connection or CA problem is
also visible in the service journal.

## 8. Create the HTTPS load balancer

### Certificate

In **ACM** in the same Region as the future load balancer:

- Request a public certificate for `chat.example.com`.
- Use DNS validation.
- Add the validation CNAME at your DNS provider (ACM can create it automatically
  in Route 53).
- Wait for status **Issued**.

### Target group

In **EC2 -> Target groups -> Create target group**:

- Target type: Instances.
- Protocol: HTTP.
- Port: `8000`.
- VPC: application VPC.
- Protocol version: HTTP1.
- Health check path: `/healthz`.
- Healthy success code: `200`.
- Register `lan-chat-app-1` on port 8000.

### Application Load Balancer

In **EC2 -> Load balancers -> Create -> Application Load Balancer**:

- Scheme: **Internet-facing**.
- IP address type: IPv4, or Dualstack if your DNS/VPC uses IPv6.
- Map at least two public subnets in different Availability Zones.
- Security group: `lan-chat-alb-sg` only.
- Listener HTTPS 443: forward to the target group and select the issued ACM
  certificate. Choose the current recommended TLS security policy.
- Listener HTTP 80: redirect to HTTPS, port 443, status 301.

Under load balancer attributes, set the idle timeout to at least 120 seconds (for
example 300) for comfortable WebSocket idle periods. The React client reconnects
if a WebSocket is interrupted.

Wait until the registered target is **Healthy**. Never expose EC2 port 8000 to a
public CIDR: Uvicorn trusts forwarded headers because the security group makes the
load balancer the only inbound caller.

## 9. Point the hostname to the load balancer

If the domain is in Route 53, open its hosted zone and create:

- Record name: `chat` (for `chat.example.com`).
- Type: A.
- Alias: Yes.
- Route traffic to: Alias to Application Load Balancer.
- Select the Region and load balancer.
- Evaluate target health: Yes.

For external DNS, use the provider's supported ALIAS/ANAME record, or a CNAME for
the `chat` subdomain, pointing to the ALB DNS name.

Open `https://chat.example.com`. Do not use the EC2 IP or ALB hostname because the
application intentionally rejects unapproved Host and Origin values.

## 10. Verification checklist

- `https://chat.example.com/healthz` returns `{"status":"ok"}`.
- `http://chat.example.com` redirects to HTTPS.
- The browser shows the ACM certificate and no mixed-content warning.
- Registration and login set a cookie named `__Host-lan_chat_session` with
  Secure, HttpOnly, SameSite=Lax, and path `/`.
- Two browsers can exchange text over a `wss://` connection.
- Image and MP4/WebM uploads create private S3 objects under randomized prefixes.
- Opening an authorized media request redirects to a short-lived signed
  CloudFront URL; the unsigned object URL and S3 URL return 403.
- RDS says **Publicly accessible: No**, and 5432 is reachable only from the EC2
  security group.
- EC2 has no long-lived AWS keys and no public inbound rule.
- CloudFront and ALB access logs, CloudTrail, RDS backups, and billing alarms are
  enabled as appropriate for your budget.

## Updating the application

Take an RDS snapshot before schema-changing releases. Then connect with Session
Manager and run:

```bash
cd /opt/lan-chat
DEPLOY_USER="$(id -un)"
git pull --ff-only
.venv/bin/pip install -r requirements.txt
npm ci
npm run build
sudo chown -R "${DEPLOY_USER}:lan-chat" /opt/lan-chat
sudo chmod -R o-rwx /opt/lan-chat
sudo systemctl restart lan-chat
sudo systemctl status lan-chat --no-pager
sudo journalctl -u lan-chat -n 100 --no-pager
```

For releases with significant traffic, put the instance in target-group draining
or use a blue/green replacement to avoid interrupting active WebSockets.

## Moving existing local data

Deployment does not automatically copy the current laptop database or `uploads/`
directory. For an in-place migration, restore a `pg_dump` backup into the empty
RDS `lan_chat` database before first use, then copy every file under `uploads/`
to the S3 bucket while preserving its relative object key. For example, from a
trusted machine signed in with AWS SSO:

```bash
aws s3 sync uploads/ s3://<BUCKET_NAME>/ --exclude ".gitkeep"
```

Do not omit or rename those keys: existing database rows refer to them. Take a
fresh database backup first and verify several old images/profile photos through
the app before retiring the laptop copy. Legacy PBKDF2 password hashes remain
valid and are upgraded to Argon2id when each user next logs in.

## Production hardening still worth adding

This deployment provides HTTPS/WSS, private network/database/storage controls,
Argon2id password hashing, opaque server-side sessions, secure cookies, origin
and host enforcement, upload type/size validation, security headers, and a basic
single-instance authentication throttle. A public consumer service should also
plan for:

- AWS WAF managed rules and a rate-based rule on the ALB. In-process throttling
  is not a substitute for distributed edge rate limiting.
- Email ownership verification, password reset, optional MFA/passkeys, account
  lockout alerts, and an abuse/reporting process.
- Alembic migrations instead of startup-time schema upgrades.
- Redis/pub-sub plus a shared presence design before horizontal scaling.
- Centralized structured logs, alarms for ALB 5xx/latency, RDS CPU/storage and
  connections, EC2 health, and application restarts.
- Dependency/security scanning, tested restore procedures, key/secret rotation,
  data-retention rules, a privacy policy, and incident-response procedures.

The app is not end-to-end encrypted: TLS protects data in transit and AWS
encryption protects stored data, but the application server can process message
content and media.

## AWS references

- [Application Load Balancer HTTPS listeners](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/create-https-listener.html)
- [Application Load Balancer WebSocket support](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-listeners.html)
- [Restrict an S3 origin with CloudFront OAC](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-s3.html)
- [CloudFront signed URLs](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-signed-urls.html)
- [RDS PostgreSQL TLS](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/PostgreSQL.Concepts.General.SSL.html)
- [RDS and EC2 in a VPC](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_VPC.Scenarios.html)
- [IAM roles for applications on EC2](https://docs.aws.amazon.com/autoscaling/ec2/userguide/us-iam-role.html)
- [Systems Manager Session Manager](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager.html)
- [Require IMDSv2](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-IMDS-new-instances.html)
