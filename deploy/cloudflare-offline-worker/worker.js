const OFFLINE_STATUSES = new Set([502, 503, 520, 521, 522, 523, 524, 530]);

const OFFLINE_HTML = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>LetsVibe — Currently Offline</title>
  <link rel="icon" type="image/png" href="https://home.todd0042.dev/assets/letsvibe-icon.webp">
  <style>
    :root {
      --bg: #070c1a;
      --card: rgba(15, 23, 42, 0.85);
      --border: rgba(56, 189, 248, 0.25);
      --cyan: #00f0ff;
      --amber: #fbbf24;
      --text: #f1f5f9;
      --muted: #94a3b8;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at 20% 20%, rgba(0, 240, 255, 0.12), transparent 45%),
        radial-gradient(circle at 80% 80%, rgba(251, 191, 36, 0.08), transparent 45%),
        var(--bg);
    }
    .panel {
      width: 100%;
      max-width: 820px;
      display: grid;
      grid-template-columns: 1.15fr 0.85fr;
      gap: 28px;
      align-items: center;
      padding: 36px;
      border-radius: 22px;
      background: var(--card);
      border: 1px solid var(--border);
      box-shadow: 0 24px 60px rgba(0, 0, 0, 0.55);
      backdrop-filter: blur(12px);
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 5px 12px;
      border-radius: 999px;
      font-size: 0.78rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--amber);
      background: rgba(251, 191, 36, 0.12);
      border: 1px solid rgba(251, 191, 36, 0.35);
      margin-bottom: 14px;
    }
    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--amber);
      box-shadow: 0 0 8px var(--amber);
    }
    h1 {
      margin: 0 0 12px;
      font-size: clamp(1.7rem, 3vw, 2.3rem);
      line-height: 1.15;
    }
    p {
      margin: 0 0 22px;
      color: var(--muted);
      line-height: 1.6;
      font-size: 0.98rem;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
    }
    .btn {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 11px 18px;
      border-radius: 11px;
      font-weight: 700;
      font-size: 0.92rem;
      text-decoration: none;
      cursor: pointer;
      border: 1px solid transparent;
      transition: transform 0.15s ease, opacity 0.15s ease;
    }
    .btn:hover { transform: translateY(-1px); }
    .btn-primary {
      background: linear-gradient(135deg, #00f0ff, #38bdf8);
      color: #04101f;
    }
    .btn-secondary {
      background: rgba(148, 163, 184, 0.12);
      color: var(--text);
      border-color: rgba(148, 163, 184, 0.28);
    }
    .sprite-wrap {
      display: flex;
      justify-content: center;
      align-items: flex-end;
    }
    .sprite-wrap img {
      max-height: 360px;
      width: auto;
      filter: drop-shadow(0 18px 28px rgba(0, 0, 0, 0.6));
    }
    @media (max-width: 720px) {
      .panel {
        grid-template-columns: 1fr;
        text-align: center;
        padding: 26px 20px;
      }
      .actions { justify-content: center; }
      .sprite-wrap img { max-height: 280px; }
    }
  </style>
</head>
<body>
  <main class="panel">
    <div>
      <div class="pill"><span class="dot"></span> Server Sleeping</div>
      <h1>LetsVibe is currently offline</h1>
      <p>
        Sorry about that! LetsVibe runs on a local home GPU server, and it is currently turned off or resting.
        Please check back a little later, or explore my other apps while it's asleep.
      </p>
      <div class="actions">
        <button class="btn btn-primary" onclick="window.location.reload()">↻ Check Again</button>
        <a class="btn btn-secondary" href="https://home.todd0042.dev">🏠 Visit Todd0042 Home</a>
      </div>
    </div>
    <div class="sprite-wrap">
      <img src="https://home.todd0042.dev/assets/todd-sad.webp" alt="Apologetic Todd illustration">
    </div>
  </main>
</body>
</html>`;

export default {
  async fetch(request) {
    try {
      const response = await fetch(request);
      if (!OFFLINE_STATUSES.has(response.status)) {
        return response;
      }
    } catch (_err) {
      // Origin unreachable / tunnel down -> fall through to offline page
    }

    const url = new URL(request.url);
    const acceptsJson = (request.headers.get("Accept") || "").includes("application/json");
    if (url.pathname.startsWith("/api/") || acceptsJson) {
      return new Response(
        JSON.stringify({
          status: "offline",
          detail: "LetsVibe server is currently offline / sleeping.",
        }),
        {
          status: 503,
          headers: {
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": "no-store",
            "Retry-After": "300",
          },
        }
      );
    }

    return new Response(OFFLINE_HTML, {
      status: 503,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
        "Retry-After": "300",
      },
    });
  },
};
