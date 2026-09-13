# AWS university prototype deployment

This guide matches the reduced deployment requested for the existing AWS setup:

```text
Browser
  -> http://<ALB_DNS_NAME>
  -> Application Load Balancer listener on HTTP 80
  -> FastAPI/Uvicorn on EC2 port 8000
       -> RDS PostgreSQL over TLS
       -> private S3 bucket through the EC2 IAM role

Authorized media request
  -> FastAPI checks the user's session and chat/group access
  -> HTTP 307 redirect to a short-lived HTTPS S3 presigned GET URL
  -> private S3 object
```

CloudFront, Route 53, a custom domain, ACM, and an HTTPS ALB listener are not
required for this prototype.

> **Prototype limitation:** login credentials, session cookies, messages, and
> WebSocket traffic between the browser and ALB travel over plain HTTP. Anyone
> able to observe that network path could read or alter them. Use this only for a
> time-limited university prototype and restore HTTPS before public or real-user
> use.

## 1. ALB and security groups

Keep the existing Application Load Balancer and target group.

### ALB listener

In **EC2 -> Load Balancers -> your ALB -> Listeners and rules**:

- Add or retain an **HTTP : 80** listener.
- Default action: **Forward to** the existing target group.
- Do not configure the port 80 listener to redirect to HTTPS.
- The HTTPS : 443 listener and ACM certificate are not needed. They may be
  removed after HTTP access is verified.

### Target group

Keep these settings:

- Target type: Instances.
- Protocol: HTTP.
- Port: `8000`.
- Health check path: `/healthz`.
- Success code: `200`.
- The EC2 instance is registered and reports **Healthy**.

### Security groups

- ALB security group inbound: TCP 80 from the networks that need the prototype.
  `0.0.0.0/0` works for a public demo, but a university/public-IP CIDR is safer
  when known.
- ALB security group outbound: TCP 8000 to the EC2 security group.
- EC2 security group inbound: TCP 8000 from the **ALB security group only**.
- Do not expose EC2 port 8000 or RDS port 5432 to `0.0.0.0/0`.
- RDS security group inbound: PostgreSQL 5432 from the EC2 security group only.

Copy the ALB's **DNS name** from its Description/Details page. It looks similar
to:

```text
lan-chat-alb-123456789.ap-south-1.elb.amazonaws.com
```

Do not include `http://`, a path, a port, or a trailing slash when using it in
`ALLOWED_HOSTS`.

## 2. Keep the S3 bucket private

In **S3 -> bucket -> Permissions** verify:

- Block Public Access: all four options remain enabled.
- Object Ownership: Bucket owner enforced/ACLs disabled.
- There is no public-read ACL or public bucket-policy statement.

If the bucket policy contains the old
`AllowCloudFrontServicePrincipalReadOnly` statement, remove that statement after
testing the new S3 URLs. It is no longer used. Do not replace it with a public
allow statement.

The browser receives presigned URLs created by the EC2 role. This does not make
the bucket or its objects public. The URL is a temporary bearer credential, so
keep the expiry short and do not log its query string.

No S3 CORS configuration is needed for the current `<img>` and `<video>` use.

## 3. Update the EC2 IAM role

The EC2 instance role still needs:

- `secretsmanager:GetSecretValue` for the RDS database secret.
- `s3:GetObject`, `s3:PutObject`, and `s3:DeleteObject` for
  `arn:aws:s3:::<BUCKET_NAME>/*`.

The checked-in template is:

```text
deploy/ec2-iam-policy.json.example
```

Replace `<DATABASE_SECRET_ARN>` and `<BUCKET_NAME>` before applying it.

Remove the old permission for the CloudFront private-key secret. The application
does not read that secret anymore. The old CloudFront secret may be scheduled for
deletion after the new deployment is verified.

Keep `AmazonSSMManagedInstanceCore` attached if Session Manager is used to manage
the EC2 instance. Do not create access keys on EC2; Boto3 automatically obtains
temporary credentials from the instance role.

## 4. Final EC2 environment variables

Edit `/etc/lan-chat/lan-chat.env` and use this exact structure, replacing only
the placeholder values:

```dotenv
APP_ENV=production
AWS_REGION=ap-south-1

DATABASE_SECRET_ID=<FULL_RDS_DATABASE_SECRET_ARN>
DB_NAME=lan_chat
RDS_CA_BUNDLE=/etc/pki/ca-trust/source/anchors/global-bundle.pem

MEDIA_BACKEND=s3
S3_BUCKET=<PRIVATE_S3_BUCKET_NAME>
S3_KMS_KEY_ID=
S3_PRESIGNED_URL_TTL_SECONDS=900

ALLOWED_HOSTS=<ALB_DNS_NAME>
ALLOWED_ORIGINS=http://<ALB_DNS_NAME>
FORCE_HTTPS=false
COOKIE_SECURE=false
SESSION_DAYS=7

MAX_IMAGE_BYTES=8388608
MAX_VIDEO_BYTES=52428800
```

Examples of the final host/origin values:

```dotenv
ALLOWED_HOSTS=lan-chat-alb-123456789.ap-south-1.elb.amazonaws.com
ALLOWED_ORIGINS=http://lan-chat-alb-123456789.ap-south-1.elb.amazonaws.com
```

Important details:

- `ALLOWED_HOSTS` has no scheme and no trailing slash.
- `ALLOWED_ORIGINS` includes `http://` and has no trailing slash.
- Do not add port 80 to either value when using the normal ALB HTTP listener.
- `DATABASE_SECRET_ID` may be a secret name, but the full ARN is clearer and is
  recommended.
- Leave `S3_KMS_KEY_ID` blank for SSE-S3. If the existing bucket uses a
  customer-managed KMS key, set its ID/ARN and keep the required KMS permissions
  on both the EC2 role and key policy.
- Remove all `CLOUDFRONT_*` lines. They are ignored and no longer required.
- Do not place database passwords or AWS access keys in this file.

## 5. Update the application on Amazon Linux

Connect with **Systems Manager Session Manager**, then run:

```bash
cd /opt/lan-chat
DEPLOY_USER="$(id -un)"

git pull --ff-only
.venv/bin/pip install -r requirements.txt
npm ci
npm run build

sudo chown -R "${DEPLOY_USER}:lan-chat" /opt/lan-chat
sudo chmod -R o-rwx /opt/lan-chat

sudo cp deploy/lan-chat.env.example /etc/lan-chat/lan-chat.env
sudo chown root:lan-chat /etc/lan-chat/lan-chat.env
sudo chmod 0640 /etc/lan-chat/lan-chat.env
sudo vi /etc/lan-chat/lan-chat.env
```

Paste the final variables from the previous section, save the file, and restart:

```bash
sudo systemctl restart lan-chat
sudo systemctl status lan-chat --no-pager
sudo journalctl -u lan-chat -n 100 --no-pager
curl --fail http://127.0.0.1:8000/healthz
```

The existing systemd unit remains valid. It runs one Uvicorn worker on port 8000
and trusts proxy headers. Port 8000 must therefore remain reachable only from the
ALB security group.

If the service fails at startup, check the journal for the exact missing setting,
RDS TLS/secret error, or IAM denial.

## 6. Verify the deployment

Replace `<ALB_DNS_NAME>` in these commands:

```bash
curl -i http://<ALB_DNS_NAME>/healthz
curl -I http://<ALB_DNS_NAME>/login
```

Then open:

```text
http://<ALB_DNS_NAME>
```

Check the following:

- The login page loads without redirecting to HTTPS.
- Registration/login works and creates the `lan_chat_session` HttpOnly cookie.
- Browser developer tools show a `ws://<ALB_DNS_NAME>/ws` connection.
- Two users can exchange text messages.
- Image and MP4/WebM uploads create objects in the private S3 bucket.
- `/api/media/<id>` or `/api/group-media/<id>` first checks the logged-in user,
  then returns an HTTP 307 redirect to an `https://...s3...amazonaws.com` URL with
  `X-Amz-*` signature parameters.
- A direct unsigned S3 object URL returns 403.
- The presigned URL stops working after its configured lifetime (15 minutes with
  the sample setting), or sooner if the EC2 role credentials are revoked.
- The ALB target stays healthy and the application log has no recurring errors.

## 7. Remove the remaining CloudFront resources

Only after media works through S3 presigned URLs:

1. Disable and then delete the CloudFront distribution if nothing else uses it.
2. Remove the CloudFront OAC statement from the S3 bucket policy.
3. Remove the CloudFront public key from its key group, then delete the key group
   and public key if unused.
4. Schedule deletion of the CloudFront private-key secret if unused.
5. Remove CloudFront-specific permissions from the EC2 role.

These cleanup actions are optional for application functionality, but they avoid
leaving unused credentials and billable resources behind.

## Current scaling limit

Continue to run exactly one Uvicorn worker and one EC2 application instance.
Online presence and WebSocket routing are stored in process memory. Multiple
workers or EC2 targets require shared pub/sub and presence storage such as Redis.

## AWS references

- [Sharing private S3 objects with presigned URLs](https://docs.aws.amazon.com/AmazonS3/latest/userguide/ShareObjectPreSignedURL.html)
- [S3 presigned URL expiration and security](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html)
- [Application Load Balancer listeners and WebSockets](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-listeners.html)
- [IAM roles for applications on EC2](https://docs.aws.amazon.com/autoscaling/ec2/userguide/us-iam-role.html)
- [RDS PostgreSQL TLS](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/PostgreSQL.Concepts.General.SSL.html)
