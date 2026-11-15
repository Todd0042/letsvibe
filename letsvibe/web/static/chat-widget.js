(function () {
  if (window.__letsvibeChatInit) return;
  window.__letsvibeChatInit = true;

  const STYLE = `
    .lv-chat-fab {
      position: fixed;
      right: 18px;
      bottom: 18px;
      z-index: 9000;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 10px 15px;
      border-radius: 999px;
      border: 1px solid rgba(56, 189, 248, 0.45);
      background: linear-gradient(135deg, rgba(15, 23, 42, 0.96), rgba(30, 41, 59, 0.96));
      color: #f8fafc;
      font-family: inherit;
      font-size: 0.88rem;
      font-weight: 600;
      cursor: pointer;
      box-shadow: 0 8px 26px rgba(0, 0, 0, 0.45);
      transition: transform 0.15s ease, border-color 0.15s ease;
    }
    .lv-chat-fab:hover {
      transform: translateY(-2px);
      border-color: #38bdf8;
    }
    .lv-chat-badge {
      display: none;
      min-width: 18px;
      height: 18px;
      padding: 0 5px;
      border-radius: 999px;
      background: #ef4444;
      color: #fff;
      font-size: 0.72rem;
      font-weight: 700;
      align-items: center;
      justify-content: center;
    }
    .lv-chat-badge.visible { display: inline-flex; }
    .lv-chat-backdrop {
      position: fixed;
      inset: 0;
      z-index: 9500;
      background: rgba(2, 6, 23, 0.72);
      backdrop-filter: blur(4px);
      display: none;
      align-items: center;
      justify-content: center;
      padding: 16px;
    }
    .lv-chat-backdrop.open { display: flex; }
    .lv-chat-modal {
      width: 100%;
      max-width: 460px;
      height: min(600px, 85vh);
      background: #0f172a;
      border: 1px solid rgba(148, 163, 184, 0.25);
      border-radius: 14px;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      box-shadow: 0 24px 60px rgba(0, 0, 0, 0.65);
      color: #e2e8f0;
    }
    .lv-chat-head {
      padding: 14px 16px;
      background: rgba(15, 23, 42, 0.98);
      border-bottom: 1px solid rgba(148, 163, 184, 0.16);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }
    .lv-chat-title {
      font-size: 0.98rem;
      font-weight: 700;
      color: #f8fafc;
    }
    .lv-chat-sub {
      font-size: 0.76rem;
      color: #94a3b8;
      margin-top: 2px;
    }
    .lv-chat-close {
      background: transparent;
      border: 1px solid rgba(148, 163, 184, 0.25);
      color: #cbd5e1;
      border-radius: 8px;
      padding: 4px 10px;
      cursor: pointer;
      font-size: 0.85rem;
    }
    .lv-chat-close:hover { border-color: #94a3b8; color: #fff; }
    .lv-chat-body {
      flex: 1;
      overflow-y: auto;
      padding: 14px;
      display: flex;
      flex-direction: column;
      gap: 10px;
      background: #0b1120;
    }
    .lv-chat-empty {
      margin: auto;
      text-align: center;
      max-width: 300px;
      color: #94a3b8;
      font-size: 0.86rem;
      line-height: 1.5;
    }
    .lv-msg-row {
      display: flex;
      flex-direction: column;
      max-width: 82%;
    }
    .lv-msg-row.user { align-self: flex-end; align-items: flex-end; }
    .lv-msg-row.admin { align-self: flex-start; align-items: flex-start; }
    .lv-msg-bubble {
      padding: 9px 12px;
      border-radius: 12px;
      font-size: 0.88rem;
      line-height: 1.45;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .lv-msg-row.user .lv-msg-bubble {
      background: #0284c7;
      color: #f8fafc;
      border-bottom-right-radius: 4px;
    }
    .lv-msg-row.admin .lv-msg-bubble {
      background: #1e293b;
      border: 1px solid rgba(148, 163, 184, 0.22);
      color: #f1f5f9;
      border-bottom-left-radius: 4px;
    }
    .lv-msg-meta {
      font-size: 0.7rem;
      color: #64748b;
      margin-top: 3px;
    }
    .lv-chat-foot {
      padding: 10px 12px;
      border-top: 1px solid rgba(148, 163, 184, 0.16);
      background: #0f172a;
      display: flex;
      gap: 8px;
      align-items: flex-end;
    }
    .lv-chat-input {
      flex: 1;
      resize: none;
      min-height: 40px;
      max-height: 110px;
      padding: 9px 11px;
      border-radius: 10px;
      border: 1px solid rgba(148, 163, 184, 0.25);
      background: #020617;
      color: #f8fafc;
      font-family: inherit;
      font-size: 0.88rem;
      outline: none;
    }
    .lv-chat-input:focus { border-color: #38bdf8; }
    .lv-chat-send {
      padding: 9px 14px;
      border-radius: 10px;
      border: none;
      background: #38bdf8;
      color: #0f172a;
      font-weight: 700;
      font-size: 0.86rem;
      cursor: pointer;
    }
    .lv-chat-send:disabled { opacity: 0.5; cursor: not-allowed; }
    .lv-chat-err {
      padding: 6px 12px;
      font-size: 0.78rem;
      color: #fca5a5;
      background: rgba(127, 29, 29, 0.35);
      display: none;
    }
    .lv-chat-err.visible { display: block; }
  `;

  function esc(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function fmtTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  }

  function init() {
    if (window.location.pathname.startsWith('/admin/messages')) return;

    const styleEl = document.createElement('style');
    styleEl.textContent = STYLE;
    document.head.appendChild(styleEl);

    const fab = document.createElement('button');
    fab.type = 'button';
    fab.className = 'lv-chat-fab';
    fab.setAttribute('aria-label', 'Message Todd');
    fab.innerHTML = `<i class="fa-solid fa-comments"></i><span>Message Todd</span><span class="lv-chat-badge" id="lv-chat-badge">0</span>`;
    document.body.appendChild(fab);

    const backdrop = document.createElement('div');
    backdrop.className = 'lv-chat-backdrop';
    backdrop.innerHTML = `
      <div class="lv-chat-modal" role="dialog" aria-modal="true" aria-label="Direct Chat with Todd">
        <div class="lv-chat-head">
          <div>
            <div class="lv-chat-title">Message Todd</div>
            <div class="lv-chat-sub">Direct in-site chat &mdash; no external email needed</div>
          </div>
          <button type="button" class="lv-chat-close" id="lv-chat-close">Close</button>
        </div>
        <div class="lv-chat-body" id="lv-chat-body">
          <div class="lv-chat-empty">Loading chat...</div>
        </div>
        <div class="lv-chat-err" id="lv-chat-err"></div>
        <form class="lv-chat-foot" id="lv-chat-form" style="display:none;">
          <textarea class="lv-chat-input" id="lv-chat-input" rows="1" maxlength="2000" placeholder="Send a message, bug report, or feedback..."></textarea>
          <button type="submit" class="lv-chat-send" id="lv-chat-send">Send</button>
        </form>
      </div>
    `;
    document.body.appendChild(backdrop);

    const badge = document.getElementById('lv-chat-badge');
    const bodyEl = document.getElementById('lv-chat-body');
    const formEl = document.getElementById('lv-chat-form');
    const inputEl = document.getElementById('lv-chat-input');
    const sendBtn = document.getElementById('lv-chat-send');
    const errEl = document.getElementById('lv-chat-err');
    let pollTimer = null;
    let isLoggedIn = null;

    function setBadge(count) {
      const n = Number(count || 0);
      if (n > 0) {
        badge.textContent = n > 99 ? '99+' : String(n);
        badge.classList.add('visible');
      } else {
        badge.classList.remove('visible');
      }
    }

    async function refreshUnread() {
      try {
        const res = await fetch('/api/me/chat/unread', { credentials: 'same-origin' });
        if (res.ok) {
          const data = await res.json();
          setBadge(data.unread || 0);
        }
      } catch (_) {}
    }

    function renderMessages(msgs) {
      if (!msgs || !msgs.length) {
        bodyEl.innerHTML = `
          <div class="lv-chat-empty">
            No messages yet. Say hi, report a bug, or ask about a grade &mdash; Todd replies right here in this chat.
          </div>
        `;
        return;
      }
      bodyEl.innerHTML = msgs.map(m => {
        const role = m.sender_role === 'admin' ? 'admin' : 'user';
        const who = role === 'admin' ? 'Todd' : 'You';
        return `
          <div class="lv-msg-row ${role}">
            <div class="lv-msg-bubble">${esc(m.body)}</div>
            <div class="lv-msg-meta">${who} &middot; ${esc(fmtTime(m.created_at))}</div>
          </div>
        `;
      }).join('');
      bodyEl.scrollTop = bodyEl.scrollHeight;
    }

    async function loadThread() {
      errEl.classList.remove('visible');
      try {
        if (isLoggedIn === null) {
          const authRes = await fetch('/api/auth/me', { credentials: 'same-origin' });
          const auth = authRes.ok ? await authRes.json() : { logged_in: false };
          isLoggedIn = Boolean(auth.logged_in);
          if (auth.is_admin) {
            window.location.href = '/admin/messages';
            return;
          }
        }
        if (!isLoggedIn) {
          formEl.style.display = 'none';
          bodyEl.innerHTML = `
            <div class="lv-chat-empty">
              <p style="margin-bottom:12px;">Sign in with GitHub to send Todd a direct message inside LetsVibe.</p>
              <a href="/sign-in" style="display:inline-block;padding:8px 14px;border-radius:8px;background:#38bdf8;color:#0f172a;font-weight:700;text-decoration:none;">Sign in with GitHub</a>
            </div>
          `;
          return;
        }
        formEl.style.display = 'flex';
        const res = await fetch('/api/me/chat?mark_read=true', { credentials: 'same-origin' });
        if (!res.ok) throw new Error('Could not load chat.');
        const data = await res.json();
        renderMessages(data.messages || []);
        setBadge(0);
      } catch (err) {
        errEl.textContent = err.message || 'Failed to load chat.';
        errEl.classList.add('visible');
      }
    }

    function openModal() {
      backdrop.classList.add('open');
      loadThread();
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(loadThread, 10000);
      setTimeout(() => { if (inputEl && formEl.style.display !== 'none') inputEl.focus(); }, 60);
    }

    function closeModal() {
      backdrop.classList.remove('open');
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }

    fab.addEventListener('click', openModal);
    document.getElementById('lv-chat-close').addEventListener('click', closeModal);
    backdrop.addEventListener('click', (e) => {
      if (e.target === backdrop) closeModal();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && backdrop.classList.contains('open')) closeModal();
    });
    document.querySelectorAll('[data-open-chat]').forEach(el => {
      el.addEventListener('click', (e) => { e.preventDefault(); openModal(); });
    });

    inputEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        formEl.requestSubmit();
      }
    });

    formEl.addEventListener('submit', async (e) => {
      e.preventDefault();
      const text = inputEl.value.trim();
      if (!text) return;
      sendBtn.disabled = true;
      errEl.classList.remove('visible');
      try {
        const res = await fetch('/api/me/chat/messages', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ body: text }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'Could not send message.');
        inputEl.value = '';
        await loadThread();
      } catch (err) {
        errEl.textContent = err.message || 'Send failed.';
        errEl.classList.add('visible');
      } finally {
        sendBtn.disabled = false;
      }
    });

    refreshUnread();
    setInterval(refreshUnread, 30000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
