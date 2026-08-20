# Companion endpoint on an Ubuntu VPS

The bot and private companion receiver run in the same `python3 main.py`
process. Nginx is only responsible for public HTTPS. The bot port must remain
bound to loopback.

## Steps that do not require root

Set these values in the bot's `.env` file. Leave `COMPANION_PUBLIC_URL` empty
until the administrator has configured DNS and HTTPS.

```dotenv
COMPANION_API_HOST=127.0.0.1
COMPANION_API_PORT=8080
COMPANION_PUBLIC_URL=
```

Start the bot normally:

```bash
python3 main.py
```

From another SSH session, verify the private receiver:

```bash
curl --fail --show-error http://127.0.0.1:8080/healthz
```

Expected response:

```json
{"status": "ok"}
```

Port 8080 should not be opened in UFW or the VPS provider firewall.

## Administrator handoff

1. Create a DNS `A` record such as `companion.example.com` pointing to the VPS.
2. Replace `companion.example.com` below with that hostname.
3. Save this as `/etc/nginx/sites-available/duckies-companion`:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name companion.example.com;

    client_max_body_size 2k;

    location = /healthz {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location = /v1/companion/matches {
        limit_except POST { deny all; }
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        return 404;
    }
}
```

4. Enable and validate it:

```bash
sudo ln -s /etc/nginx/sites-available/duckies-companion /etc/nginx/sites-enabled/duckies-companion
sudo nginx -t
sudo systemctl reload nginx
```

5. Install Certbot's Nginx integration and request the certificate:

```bash
sudo apt update
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d companion.example.com
```

6. Verify the public route:

```bash
curl --fail --show-error https://companion.example.com/healthz
```

7. Set the final origin in `.env` and restart the bot:

```dotenv
COMPANION_PUBLIC_URL=https://companion.example.com
```

## Pairing after HTTPS is ready

Run `/deadlock companion-pair` in Discord. Its ephemeral response contains the
endpoint and a newly generated bearer token. On the gaming PC, use those values
and start:

```powershell
duckies-companion --launch
```

The receiver validates the token, event source, timestamp, and match ID; limits
each token to five requests per minute; and stores accepted user/server/match
tuples so retries cannot create duplicate watches.
