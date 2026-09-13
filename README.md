# LAN Chat

A real-time FastAPI and React chat application that can run either on a local LAN or on AWS.

```text
Client A/B/C → HTTP + WebSocket/TCP → FastAPI (0.0.0.0:8000) → PostgreSQL
```

## Features

- Register and sign in with cookie-based sessions
- Automatically generated public IDs such as `CHAT-A1B2C3D4`
- Find a user by public ID or username and send a connection request
- Accept or reject incoming requests
- See accepted connections and live online/offline presence
- Open a private chat, load message history, and exchange messages instantly
- Send protected PNG, JPEG, GIF, and WebP images up to 8 MB, plus MP4/WebM videos up to 50 MB
- Paste screenshots directly into the private or group chat composer
- Edit your own text messages and image captions, or delete messages for everyone, in private and group chats
- Insert emojis, reply with quoted context, forward messages to chats or groups, and add live emoji reactions
- See live private-chat read receipts and open pending connection requests from the notification bell
- Open profiles from group sender names, customize your photo, display name, status, and bio
- Disconnect from a user and erase only the private history; shared group messages are preserved
- Upload a profile photo using Change photo on the connections page (PNG, JPEG, GIF, or WebP; up to 8 MB)
- See a live typing indicator while a contact is composing a message
- Create group chats from accepted connections and add members later as the owner
- Exchange persistent text, image, and video messages in groups with group typing indicators
- Responsive React interface with locally bundled fonts and icons
- Persist users, sessions, requests, connections, and messages in PostgreSQL

## Run it

Requirements: Python 3.11+, Node.js 20+, Docker Desktop (or an existing PostgreSQL server), and all devices on the same LAN.

1. Start PostgreSQL:

   ```powershell
   docker compose up -d postgres
   ```

   The container listens on host port `5433`, leaving the standard `5432` port
   available for any PostgreSQL installation already running on Windows.

2. Create a virtual environment and install packages:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

3. Build the React frontend after changing anything in `frontend/`:

   ```powershell
   npm install
   npm run build
   ```

   The compiled app is written to `app/frontend_dist/` and is served by FastAPI.
   Dependencies, fonts, and icons are bundled locally, so client devices do not
   need Internet access.

4. Optionally copy `.env.example` to `.env` and set `DATABASE_URL`. Environment values are read by the process; a `.env` file is not loaded automatically unless your shell or process manager loads it.

5. Start exactly one application worker with the convenience launcher:

   ```powershell
   python start_server.py
   ```

   When startup finishes, the terminal prints a clickable link similar to:

   ```text
   Other devices: http://192.168.1.25:8000  <-- copy this
   ```

   You can choose another port with `python start_server.py --port 8080`.

6. Copy the displayed **Other devices** link into a browser on the phone or laptop connected to the same LAN. If Windows Firewall prompts, allow Python on **Private networks** only.

## AWS production deployment

The production configuration uses an HTTPS Application Load Balancer, EC2,
private RDS PostgreSQL, private S3 media storage, CloudFront signed URLs, IAM
roles, and Secrets Manager. Follow [AWS_DEPLOYMENT.md](AWS_DEPLOYMENT.md) for the
complete console selections and Amazon Linux commands.

## Notes for local LAN mode

- Use one Uvicorn worker. Online presence and WebSocket routing are held in that process. Scaling to several workers would require a shared pub/sub layer such as Redis.
- Traffic is plain HTTP/WebSocket on the trusted LAN. Passwords are salted and hashed, but messages are not end-to-end encrypted and network traffic is not TLS-encrypted.
- Database tables are created automatically on startup. For a longer-lived project, add Alembic migrations.
- Image files are stored under `uploads/`; PostgreSQL stores their protected message metadata. Back up both PostgreSQL and this directory if the data matters.
- Deleting a message clears its text, removes its stored image, and shows a deleted placeholder. Previously downloaded copies cannot be revoked.
- PostgreSQL in `docker-compose.yml` is exposed only to simplify local development. Change the sample credentials before using the app outside a trusted test environment.
- Router client isolation, guest Wi-Fi, VPNs, or firewall rules can prevent devices from reaching one another.

Interactive API documentation is available to the server operator at `http://localhost:8000/docs`.
