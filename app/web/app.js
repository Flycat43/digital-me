/* digital-me 桌宠前端：SSE 流式对话 → 句级 TTS → 口型 → 延迟埋点（R7） */
'use strict';

const $ = (s) => document.querySelector(s);
const petEl = $('#pet');
const logEl = $('#log');
const inputEl = $('#input');
const btnSend = $('#btnSend');
const statEl = $('#stat');

const state = {
  busy: false,
  history: [],           // 仅用于 UI（服务端另存）
  ttsOk: null,           // null=未试过 true=可用 false=不可用
  audioCtx: null,
  analyser: null,
  mouthRAF: 0,
};

/* ---------------- 视觉状态 ---------------- */
function setState(s, label) {
  petEl.className = 'state-' + s;
  if (label) $('#badgeText').textContent = label;
}

/* ---------------- 呼吸之外的微动作 ---------------- */
(function blinkLoop() {
  const eyes = [$('#eyeL'), $('#eyeR')];
  function blink() {
    const t0 = performance.now();
    (function step(now) {
      const p = Math.min(1, (now - t0) / 150);
      const k = p < 0.5 ? 1 - p * 2 : (p - 0.5) * 2;   // 1→0→1
      const ry = 1.5 + 9.5 * k;
      eyes.forEach((e) => e && e.setAttribute('ry', ry.toFixed(2)));
      if (p < 1) requestAnimationFrame(step);
    })(t0);
    setTimeout(blink, 2200 + Math.random() * 4200);
  }
  setTimeout(blink, 1200);
})();

let mouthBase = 'M138 190 q12 8 24 0';
function setMouth(open) {           // open: 0..1
  const h = 1 + open * 13;
  const w = 12 + open * 2;
  $('#mouth').setAttribute('d', `M${150 - w} 189 q${w} ${h} ${2 * w} 0`);
}

/* ---------------- 音频（口型同步） ---------------- */
function audioCtx() {
  if (!state.audioCtx) {
    state.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    state.analyser = state.audioCtx.createAnalyser();
    state.analyser.fftSize = 512;
    state.analyser.connect(state.audioCtx.destination);
  }
  return state.audioCtx;
}

function playBuffer(buf) {
  return new Promise((resolve) => {
    const ctx = audioCtx();
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(state.analyser);
    const data = new Uint8Array(state.analyser.fftSize);
    src.onended = () => { cancelAnimationFrame(state.mouthRAF); setMouth(0); resolve(); };
    src.start();
    const t0 = performance.now();
    (function loop() {
      state.analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (let i = 0; i < data.length; i++) { const v = (data[i] - 128) / 128; sum += v * v; }
      const rms = Math.sqrt(sum / data.length);
      setMouth(Math.min(1, rms * 7));
      state.mouthRAF = requestAnimationFrame(loop);
    })();
    state.audioStartedAt = Date.now();
    state.audioStartDelay = performance.now() - t0;
    resolve();
  });
}

async function speak(text) {
  const t0 = performance.now();
  const res = await fetch('/api/tts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  const firstByteMs = performance.now() - t0;
  const firstByteAt = Date.now();               // 服务端第一个音频字节到达（≈响应头时刻）
  if (!res.ok) {
    const info = await res.json().catch(() => ({}));
    throw new Error(info.error || ('TTS ' + res.status));
  }
  const provider = res.headers.get('X-TTS-Provider') || '?';
  const ab = await res.arrayBuffer();            // 下载完
  const buf = await audioCtx().decodeAudioData(ab);  // 解码完，可以播了
  return { buf, firstByteMs, firstByteAt, provider };
}

/* ---------------- 句子切分：首句尽早出声 ---------------- */
const SENT_END = /[。！？!?…\n]/;

function splitSentences(text) {
  const out = [];
  let cur = '';
  for (const ch of text) {
    cur += ch;
    if (SENT_END.test(ch) && cur.trim().length >= 2) { out.push(cur.trim()); cur = ''; }
    else if (cur.length >= 40 && /[，,；;]/.test(ch)) { out.push(cur.trim()); cur = ''; }
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

/* ---------------- 延迟埋点（R7） ---------------- */
async function logLatency(rec) {
  try {
    await fetch('/api/latency', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(rec),
    });
  } catch (e) { console.warn('latency log failed', e); }
}

/* ---------------- 一轮对话 ---------------- */
async function turn(text) {
  if (state.busy) return;
  state.busy = true;
  btnSend.disabled = true;
  inputEl.value = '';

  const tUserSend = Date.now();
  const turnId = 't' + tUserSend.toString(36) + Math.random().toString(36).slice(2, 6);

  addMsg('me', text);
  const botEl = addMsg('bot', '');
  const caret = document.createElement('span');
  caret.className = 'caret';
  botEl.appendChild(caret);

  setState('think', '思考中');
  statEl.textContent = '等待首个字…';

  let tFirstToken = null;
  let full = '';
  let doneInfo = null;
  let firstAudioStarted = null;
  let firstByteAt = null;
  let ttsProvider = null;
  let audioChain = Promise.resolve();
  let sentenceIdx = 0;

  const flushSentence = (s) => {
    const idx = sentenceIdx++;
    const p = (async () => {
      const { buf, firstByteAt: fb, provider } = await speak(s);
      if (idx === 0) { firstByteAt = fb; ttsProvider = provider; }
      await playBuffer(buf);
      if (idx === 0) firstAudioStarted = state.audioStartedAt;
    })();
    p.catch((e) => {
      if (state.ttsOk === null) {
        state.ttsOk = false;
        statEl.textContent = '音色未就绪（TTS 503），本轮只出字';
      }
      console.warn('tts failed', e.message);
    });
    return p;
  };

  try {
    const res = await fetch('/api/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, turn_id: turnId }),
    });
    if (!res.ok || !res.body) throw new Error('chat HTTP ' + res.status);

    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    let ttsPending = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const raw = buf.slice(0, i); buf = buf.slice(i + 2);
        const ev = /^event: (.*)$/m.exec(raw);
        const dt = /^data: (.*)$/m.exec(raw);
        if (!dt) continue;
        const type = ev ? ev[1].trim() : 'message';
        const data = JSON.parse(dt[1]);

        if (type === 'token') {
          if (tFirstToken === null) {
            tFirstToken = Date.now();
            caret.remove();
            setState('speak', '说话中');
            statEl.textContent = `首字 ${tFirstToken - tUserSend} ms`;
          }
          full += data.text;
          botEl.textContent = full;
          logEl.scrollTop = logEl.scrollHeight;

          // 边说边合成：凑满一句就送去 TTS，首句不等整段生成
          ttsPending += data.text;
          let m;
          while ((m = /^([\s\S]{1,80}?[。！？!?…\n])/.exec(ttsPending)) && m[1].trim().length >= 2) {
            const sent = m[1].trim();
            ttsPending = ttsPending.slice(m[1].length);
            audioChain = audioChain.then(() => flushSentence(sent)).catch(() => {});
          }
          if (ttsPending.length >= 60) {
            const cut = Math.max(ttsPending.lastIndexOf('，'), ttsPending.lastIndexOf('、'));
            if (cut > 10) {
              const sent = ttsPending.slice(0, cut + 1).trim();
              ttsPending = ttsPending.slice(cut + 1);
              audioChain = audioChain.then(() => flushSentence(sent)).catch(() => {});
            }
          }
        } else if (type === 'done') {
          doneInfo = data;
        } else if (type === 'error') {
          throw new Error(data.message);
        }
      }
    }

    caret.remove();
    // 收尾：把最后半句也念出来
    if (ttsPending.trim()) {
      const rest = ttsPending.trim();
      ttsPending = '';
      audioChain = audioChain.then(() => flushSentence(rest)).catch(() => {});
    }
    await Promise.race([audioChain, new Promise((r) => setTimeout(r, 20000))]);

  } catch (e) {
    caret.remove();
    setState('error', '出错了');
    addMsg('err', String(e.message || e));
    statEl.textContent = '出错：' + (e.message || e);
  } finally {
    state.busy = false;
    btnSend.disabled = false;
    if (petEl.className !== 'state-error') setTimeout(() => { if (!state.busy) setState('idle', '待机'); }, 1200);
  }

  const tEnd = Date.now();
  const rec = {
    turn_id: turnId,
    t_user_send: tUserSend,
    t_llm_first_token: tFirstToken,
    t_tts_first_audio: firstByteAt,
    t_audio_start: firstAudioStarted,
    ttft_ms: tFirstToken ? tFirstToken - tUserSend : null,
    tts_first_ms: firstByteAt && tFirstToken ? firstByteAt - tFirstToken : null,
    e2e_audio_ms: firstAudioStarted ? firstAudioStarted - tUserSend : null,
    total_ms: tEnd - tUserSend,
    chars: full.length,
    prompt_tokens: doneInfo && doneInfo.usage ? doneInfo.usage.prompt_eval_count : null,
    gen_tokens: doneInfo && doneInfo.usage ? doneInfo.usage.eval_count : null,
    tts_provider: ttsProvider,
    asr: 'none',
    persona: (window.__cfg && window.__cfg.name) || '',
    model: (window.__cfg && window.__cfg.llm && window.__cfg.llm.model) || '',
  };
  logLatency(rec);

  if (rec.ttft_ms) {
    statEl.textContent = `首字 ${rec.ttft_ms} ms · 首音 ${rec.e2e_audio_ms ? rec.e2e_audio_ms + ' ms' : '—'} · ${rec.chars} 字`;
  }
}

/* ---------------- UI 辅助 ---------------- */
function addMsg(kind, text) {
  const d = document.createElement('div');
  d.className = 'msg ' + kind;
  d.textContent = text;
  logEl.appendChild(d);
  logEl.scrollTop = logEl.scrollHeight;
  return d;
}

$('#composer').addEventListener('submit', (e) => {
  e.preventDefault();
  const t = inputEl.value.trim();
  if (!t || state.busy) return;
  turn(t);
});

$('#btnReset').addEventListener('click', async () => {
  await fetch('/api/reset', { method: 'POST' });
  [...logEl.querySelectorAll('.msg')].forEach((n) => n.remove());
  addMsg('hint', '上下文已清空。').className = 'hint';
  statEl.textContent = '已清空';
});

$('#btnMin').addEventListener('click', () => {
  const p = $('#panel');
  p.style.display = p.style.display === 'none' ? 'flex' : 'none';
});

/* ---------------- 启动自检 ---------------- */
(async function boot() {
  try {
    const cfg = await (await fetch('/api/config')).json();
    window.__cfg = cfg;
    $('#petName').textContent = cfg.name || '颜小二';
    $('#sub').textContent = `本地 · ${cfg.llm.model}`;
    $('#ver').textContent = `v${cfg.version}`;
    const h = await (await fetch('/api/health')).json().catch(() => ({}));
    if (h && h.ok) {
      setState('idle', '待机');
      statEl.textContent = '就绪';
    } else {
      setState('error', 'LLM 未就绪');
      statEl.textContent = 'ollama 不可达或模型未导入';
    }
    if (!cfg.asr_ready) $('#btnMic').title = 'ASR 未接入（缺识别模型）';
  } catch (e) {
    statEl.textContent = '自检失败：' + e.message;
  }
  $('#input').focus();
})();

/* 双击角色 = 清空上下文 */
$('#pet').addEventListener('dblclick', () => $('#btnReset').click());

/* ---------------- 演示/截图模式 ----------------
   ?bg=1    给页面铺一层假桌面底（截图用，真实运行时窗口是透明的）
   ?demo=1  自动问一句，用来录 GIF；?q=... 可换台词
*/
(function demoMode() {
  const q = new URLSearchParams(location.search);
  if (q.get('bg') === '1') {
    document.body.style.background =
      'radial-gradient(1200px 820px at 22% 8%, #2b3d59 0%, #182333 46%, #0c1017 100%)';
  }
  if (q.get('demo') === '1') {
    const text = q.get('q') || '最近有点累，说句让我高兴的';
    setTimeout(() => {
      inputEl.value = text;
      $('#composer').dispatchEvent(new Event('submit', { cancelable: true }));
    }, 1600);
  }
})();
