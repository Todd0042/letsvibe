export interface SessionUser {
  username: string;
  github_id: number;
  avatar_url?: string;
  is_admin?: boolean;
  exp: number;
}

export function isAdminUser(user: SessionUser | null): boolean {
  if (!user) return false;
  if (user.is_admin) return true;
  return (
    String(user.username || "").trim().toLowerCase() === "todd0042" ||
    Number(user.github_id) === 205512876 ||
    Number(user.github_id) === 69619147
  );
}

const b64uEncode = (bytes: Uint8Array): string =>
  btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

const b64uDecode = (str: string): Uint8Array => {
  const pad = str.length % 4 ? "=".repeat(4 - (str.length % 4)) : "";
  const bin = atob(str.replace(/-/g, "+").replace(/_/g, "/") + pad);
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
};

async function hmacKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"],
  );
}

export async function signToken(payload: Record<string, unknown>, secret: string): Promise<string> {
  const body = b64uEncode(new TextEncoder().encode(JSON.stringify(payload)));
  const key = await hmacKey(secret);
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(body)));
  return `${body}.${b64uEncode(sig)}`;
}

export async function verifyToken(token: string, secret: string): Promise<SessionUser | null> {
  const parts = String(token || "").split(".");
  if (parts.length !== 2) return null;
  try {
    const key = await hmacKey(secret);
    const ok = await crypto.subtle.verify(
      "HMAC",
      key,
      b64uDecode(parts[1]),
      new TextEncoder().encode(parts[0]),
    );
    if (!ok) return null;
    const data = JSON.parse(new TextDecoder().decode(b64uDecode(parts[0]))) as SessionUser;
    if (!data?.username || (data.exp && Date.now() / 1000 > data.exp)) return null;
    return data;
  } catch {
    return null;
  }
}

export async function extractUser(req: Request, secret: string): Promise<SessionUser | null> {
  const auth = (req.headers.get("authorization") || "").trim();
  if (auth.toLowerCase().startsWith("bearer ")) {
    const tok = auth.slice(7).trim();
    if (tok) return verifyToken(tok, secret);
  }
  return null;
}

export function popupAuthHtml(token: string, user: SessionUser): string {
  const payload = JSON.stringify({ type: "letsvibe_anna_auth", token, user });
  return `<!DOCTYPE html><html><body><script>
    if (window.opener) {
      window.opener.postMessage(${payload}, "*");
    }
    window.close();
  </script><p>Signed in as @${user.username}. You can close this window.</p></body></html>`;
}
