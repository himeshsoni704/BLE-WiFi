# Public dashboard link with Vercel (backend stays on your laptop)

The dashboard is static files, so Vercel can host it. The backend (FastAPI, SQLite, the models, WebSocket) cannot run on
Vercel, so it keeps running on your laptop and an HTTPS tunnel makes it reachable. **Your data stays in
`campus-presence/backend/data/campus.db` on the laptop; Vercel never stores any of it.**

```
browser ──> https://your-project.vercel.app   (static dashboard)
   │
   └──────> https://xxxx.trycloudflare.com ──tunnel──> http://localhost:8000  (your laptop: backend + database)
```

The link only works while the laptop is on, the backend is running and the tunnel is up.

## 1. Start the backend, allowing the Vercel origin

Browsers block a site from calling a different origin unless the backend allows it (`CORS_ORIGINS`). Use your exact Vercel
address (you can list several, comma separated):

```bash
cd campus-presence/backend
source .venv/bin/activate                                    # Windows: .venv\Scripts\activate
export CORS_ORIGINS=https://your-project.vercel.app,http://localhost:5173
uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
```

```powershell
# Windows PowerShell
$env:CORS_ORIGINS = "https://your-project.vercel.app,http://localhost:5173"
uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
```

(`CORS_ORIGINS=*` also works for a quick test. Logins still need a password, but prefer the exact address.)

## 2. Open the tunnel (second terminal)

Cloudflare Tunnel, no account needed for a quick tunnel:

```bash
cloudflared tunnel --url http://localhost:8000
```

It prints an address like `https://random-words.trycloudflare.com`. Install: `winget install Cloudflare.cloudflared`
(Windows) or `brew install cloudflared` (macOS). `ngrok http 8000` works too (the dashboard adds the header ngrok needs).
Check it: open `<tunnel address>/health` and you should see `{"ok":true,...}`.

## 3. Deploy the dashboard to Vercel

1. vercel.com → **Add New → Project** → import `himeshsoni704/BLE-WiFi`, branch `Latest-ble-wifi-ml` (or the branch you want).
2. **Root Directory:** `campus-presence/frontend`.
3. Leave the rest. `vercel.json` in that folder sets the Vite build, the output folder, and the single-page-app rewrite.
   No environment variables are needed.
4. **Deploy.**

## 4. Sign in

Open your Vercel address. The sign-in page has an extra **Backend URL** field: paste the tunnel address, then sign in as
`faculty` / `demo1234`. The address is remembered in that browser, so **when the tunnel address changes (a quick tunnel gets
a new one each time) you only paste the new one. No rebuild or redeploy.**

## Things to know

- **The tunnel makes your laptop's backend public.** Anyone with the address can reach the sign-in page. Demo accounts use
  the password `demo1234`, so before sharing the link set a different one: `DEMO_PASSWORD=<something>` (then delete
  `campus.db` once so the accounts are recreated), or `DEMO_MODE=0` and create your own accounts. Stop the tunnel after the demo.
- **Phones don't need the tunnel.** Keep the app's Server field on the LAN address (`http://<laptop-IP>:8000`); it is faster.
- **Mixed content:** the Vercel page is HTTPS, so the backend address must be HTTPS too. That is why a tunnel is used;
  `http://<laptop-IP>:8000` will not work from a Vercel page.
- **Local use is unchanged.** `http://localhost:8000/ui/` (built by `npm run build`) still works exactly as before. The
  Backend URL field only exists on the Vercel build.
- **Not a production setup.** For a stable, always-on link, host the backend on a small server with a persistent disk
  instead; it would start with its own empty database.
