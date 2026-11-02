// Scan-first feedback (build plan P1 #1): a distinct sound and flash for success and for error, so the packer
// knows without looking at the screen. Web Audio, no files to load. Muted state is remembered per browser.
let ctx = null;
const muted = () => { try { return localStorage.getItem("babylon-mute") === "1"; } catch { return false; } };

export function setMuted(v) {
  try { localStorage.setItem("babylon-mute", v ? "1" : "0"); } catch { /* private window */ }
}
export const isMuted = muted;

function tone(freq, ms, at = 0, type = "sine", gain = 0.12) {
  if (muted()) return;
  try {
    ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
    const t = ctx.currentTime + at;
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.type = type; o.frequency.value = freq;
    g.gain.setValueAtTime(gain, t);
    g.gain.exponentialRampToValueAtTime(0.0001, t + ms / 1000);
    o.connect(g).connect(ctx.destination);
    o.start(t); o.stop(t + ms / 1000 + 0.02);
  } catch { /* no audio device */ }
}

function flash(kind) {
  const el = document.body;
  el.classList.remove("flash-ok", "flash-bad");
  void el.offsetWidth;  // restart the animation
  el.classList.add(`flash-${kind}`);
  setTimeout(() => el.classList.remove(`flash-${kind}`), 450);
}

export function ok() { tone(880, 90); tone(1320, 110, 0.09); flash("ok"); }
export function bad() { tone(220, 170, 0, "square", 0.08); tone(180, 220, 0.2, "square", 0.08); flash("bad"); }
