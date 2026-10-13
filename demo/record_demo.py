"""코드로 찍는 시연 영상: Streamlit 앱을 Playwright로 조작하고 CDP 스크린캐스트로 녹화해 MP4로 만든다.

실행:  python demo/record_demo.py          (프로젝트 루트에서)
결과:  demo/out/demo.mp4

안전장치
- 앱을 localhost 에서 직접 띄우며(배포 서버 접속 X), 자식 프로세스의 SUPABASE_URL/KEY 를 비워서
  분석 기록이 DB 에 저장되지 않게 한다 (db.is_enabled() == False). → 시드/삭제가 필요 없음.
- 실패해도 finally 에서 Streamlit 프로세스 트리를 정리한다.
"""
from __future__ import annotations

import base64
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import imageio_ffmpeg
from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "demo" / "out"
FRAMES = OUT / "frames"
PORT = 8599
URL = f"http://localhost:{PORT}"
VIEWPORT = {"width": 1440, "height": 810}  # 16:9, ffmpeg 에서 1920x1080 으로 스케일
COMPANY = "삼성물산"

# ── 오버레이: 자막 / 가짜 커서 / 하이라이트 / 타이틀 카드 ─────────────────────────
OVERLAY_JS = r"""
(() => {
  const SS = window.sessionStorage;
  const save = (k, v) => { try { SS.setItem(k, JSON.stringify(v)); } catch (e) {} };
  const load = (k) => { try { return JSON.parse(SS.getItem(k)); } catch (e) { return null; } };

  const css = `
    #dm-cap{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);z-index:2147483600;
      max-width:1100px;display:none;align-items:center;gap:14px;padding:14px 22px;border-radius:14px;
      background:rgba(10,10,10,.88);border:1px solid #3987e5;color:#f0efec;font:600 20px/1.45 "Pretendard Variable",system-ui,sans-serif;
      box-shadow:0 8px 30px rgba(0,0,0,.5);pointer-events:none}
    #dm-cap .b{flex:none;min-width:30px;height:30px;border-radius:8px;background:#3987e5;color:#fff;
      font:800 16px/30px "JetBrains Mono",monospace;text-align:center;padding:0 6px}
    #dm-cur{position:fixed;left:0;top:0;width:26px;height:26px;z-index:2147483640;pointer-events:none;
      opacity:.85;transform:translate(-3px,-2px);display:none}
    #dm-box{position:fixed;z-index:2147483500;pointer-events:none;display:none;border:2px solid #3987e5;
      border-radius:12px;box-shadow:0 0 0 9999px rgba(0,0,0,.55);transition:all .25s ease}
    #dm-title{position:fixed;inset:0;z-index:2147483647;display:none;flex-direction:column;align-items:center;
      justify-content:center;gap:16px;background:#0d0d0d;color:#f0efec;text-align:center;
      font-family:"Pretendard Variable",system-ui,sans-serif}
    #dm-title h1{font-size:52px;font-weight:800;margin:0;letter-spacing:-.02em}
    #dm-title p{font-size:24px;color:#898781;margin:0}
    #dm-title i{width:64px;height:64px;border-radius:14px;background:#3987e5;display:block;font-style:normal;
      font-size:34px;line-height:64px}
  `;

  function build() {
    if (document.getElementById('dm-cap')) return;
    const st = document.createElement('style'); st.textContent = css; document.documentElement.appendChild(st);
    const mk = (id, html) => { const d = document.createElement('div'); d.id = id; if (html) d.innerHTML = html;
      document.documentElement.appendChild(d); return d; };
    mk('dm-cap', '<span class="b"></span><span class="t"></span>');
    mk('dm-box');
    mk('dm-cur', '<svg viewBox="0 0 24 24" width="26" height="26"><path d="M3 2l7 19 3-8 8-3z" fill="#fff" stroke="#111" stroke-width="1.5" stroke-linejoin="round"/></svg>');
    mk('dm-title', '<i>🌍</i><h1></h1><p></p>');
    const cap = load('cap'); if (cap) api.say(cap.step, cap.text);
    const ttl = load('title'); if (ttl) api.title(ttl.h, ttl.p);
    const pos = load('pos'); if (pos) api.cursor(pos.x, pos.y);
  }

  const api = {
    say(step, text) {
      save('cap', text ? {step, text} : null);
      const c = document.getElementById('dm-cap'); if (!c) return;
      if (!text) { c.style.display = 'none'; return; }
      c.querySelector('.b').textContent = step; c.querySelector('.b').style.display = step ? 'block' : 'none';
      c.querySelector('.t').textContent = text; c.style.display = 'flex';
    },
    cursor(x, y) {
      save('pos', {x, y});
      const c = document.getElementById('dm-cur'); if (!c) return;
      c.style.display = 'block'; c.style.left = x + 'px'; c.style.top = y + 'px';
    },
    box(x, y, w, h) {
      const b = document.getElementById('dm-box'); if (!b) return;
      if (w == null) { b.style.display = 'none'; return; }
      Object.assign(b.style, {display: 'block', left: (x - 6) + 'px', top: (y - 6) + 'px',
        width: (w + 12) + 'px', height: (h + 12) + 'px'});
    },
    title(h, p) {
      save('title', h ? {h, p} : null);
      const t = document.getElementById('dm-title'); if (!t) return;
      if (!h) { t.style.display = 'none'; return; }
      t.querySelector('h1').textContent = h; t.querySelector('p').textContent = p || ''; t.style.display = 'flex';
    },
  };
  window.__dm = api;
  window.addEventListener('mousemove', (e) => api.cursor(e.clientX, e.clientY), true);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build); else build();
})();
"""


# ── Streamlit 서버 ────────────────────────────────────────────────────────────
def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def start_app() -> subprocess.Popen:
    if port_in_use(PORT):
        sys.exit(f"포트 {PORT} 가 이미 사용 중입니다. 먼저 종료하세요.")
    env = {**os.environ, "SUPABASE_URL": "", "SUPABASE_KEY": "", "PYTHONUTF8": "1"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app.py", "--server.port", str(PORT),
         "--server.headless", "true", "--browser.gatherUsageStats", "false"],
        cwd=ROOT / "src", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        try:
            urllib.request.urlopen(f"{URL}/_stcore/health", timeout=1)
            return proc
        except Exception:  # noqa: BLE001
            time.sleep(1)
    stop_app(proc)
    sys.exit("Streamlit 이 시간 안에 뜨지 않았습니다.")


def stop_app(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        else:
            proc.terminate()


# ── CDP 스크린캐스트 ──────────────────────────────────────────────────────────
class Recorder:
    def __init__(self, page: Page):
        self.page = page
        self.cdp = page.context.new_cdp_session(page)
        self.frames: list[tuple[float, Path]] = []
        self.paused = False
        self.paused_total = 0.0
        self._pause_start = 0.0
        self._t0 = 0.0
        self.cdp.on("Page.screencastFrame", self._on_frame)

    def _now(self) -> float:
        return time.monotonic() - self._t0 - self.paused_total

    def _on_frame(self, ev) -> None:
        self.cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})
        if self.paused:
            return
        path = FRAMES / f"{len(self.frames):06d}.jpg"
        path.write_bytes(base64.b64decode(ev["data"]))
        self.frames.append((self._now(), path))

    def start(self) -> None:
        self._t0 = time.monotonic()
        self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 90, "everyNthFrame": 1})

    def pause(self) -> None:  # 로딩 같은 지루한 구간을 잘라낸다
        self.paused, self._pause_start = True, time.monotonic()

    def resume(self) -> None:
        self.paused_total += time.monotonic() - self._pause_start
        self.paused = False

    def stop(self) -> float:
        self.page.wait_for_timeout(300)
        self.cdp.send("Page.stopScreencast")
        return self._now()


def encode(frames: list[tuple[float, Path]], end_t: float, dest: Path) -> None:
    """스크린캐스트는 화면이 바뀔 때만 프레임을 주므로, 타임스탬프 차이를 duration 으로 쓰는 concat 리스트를 만든다."""
    lines = []
    for i, (t, path) in enumerate(frames):
        nxt = frames[i + 1][0] if i + 1 < len(frames) else end_t
        lines.append(f"file '{path.name}'\nduration {max(nxt - t, 0.01):.4f}")
    lines.append(f"file '{frames[-1][1].name}'")  # concat demuxer 는 마지막 파일을 한 번 더 요구한다
    (FRAMES / "list.txt").write_text("\n".join(lines), encoding="utf-8")
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0", "-i", "list.txt",
         "-vf", "scale=1920:1080:flags=lanczos:in_range=pc:out_range=tv,fps=30,format=yuv420p",
         "-c:v", "libx264", "-crf", "18", "-movflags", "+faststart", "-an", str(dest.resolve())],
        cwd=FRAMES, check=True, capture_output=True,
    )


# ── Director: 자막·커서·하이라이트를 곁들여 앱을 조작 ──────────────────────────
class Director:
    def __init__(self, page: Page, rec: Recorder):
        self.page, self.rec = page, rec

    def wait(self, ms: int) -> None:
        self.page.wait_for_timeout(ms)

    def say(self, step, text: str, hold: float | None = None) -> None:
        self.page.evaluate("([s, t]) => window.__dm.say(s, t)", [step, text])
        # 글자 수에 비례해 읽는 시간을 준다
        self.wait(int((hold if hold is not None else max(2.2, len(text) * 0.09)) * 1000))

    def title(self, h: str, p: str, hold: float = 3.0) -> None:
        self.page.evaluate("([h, p]) => window.__dm.title(h, p)", [h, p])
        self.wait(int(hold * 1000))
        self.page.evaluate("() => window.__dm.title('', '')")
        self.wait(400)

    def move_to(self, loc) -> tuple[float, float]:
        bb = loc.bounding_box()
        x, y = bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2
        self.page.mouse.move(x, y, steps=35)
        self.wait(250)
        return x, y

    def click(self, loc) -> None:
        self.move_to(loc)
        self.wait(300)
        self.page.mouse.down()
        self.wait(90)
        self.page.mouse.up()

    def type(self, loc, text: str) -> None:
        self.click(loc)
        self.page.keyboard.press("Control+A")
        self.page.keyboard.press("Delete")
        self.page.keyboard.type(text, delay=140)
        self.page.keyboard.press("Enter")

    def highlight(self, loc, scroll: bool = True) -> None:
        if scroll:
            loc.evaluate("el => el.scrollIntoView({behavior: 'smooth', block: 'center'})")
            self.wait(1000)
        bb = loc.bounding_box()
        vh = VIEWPORT["height"]
        top, bottom = max(bb["y"], 8), min(bb["y"] + bb["height"], vh - 110)  # 하단 자막 영역은 피한다
        self.page.evaluate("([x, y, w, h]) => window.__dm.box(x, y, w, h)",
                           [bb["x"], top, bb["width"], max(bottom - top, 20)])

    def unhighlight(self) -> None:
        self.page.evaluate("() => window.__dm.box()")


def card_of(page: Page, head_text: str):
    """card-head 문구로 해당 카드(테두리 컨테이너)를 찾는다."""
    head = page.locator("div.card-head", has_text=head_text).first
    return head.locator("xpath=ancestor::div[@data-testid='stLayoutWrapper'][1]")


# ── 장면 (README 의 번호와 1:1 대응) ───────────────────────────────────────────
def run_scenes(page: Page, rec: Recorder) -> None:
    d = Director(page, rec)

    d.title("AI 탄소배출량 추정기", "재무데이터만으로 Scope 1·2·3 배출량을 추정합니다", hold=3.2)

    # 1) 소개
    d.say(1, "매출액·매출원가 같은 재무데이터만으로 기업의 온실가스 배출량을 추정하는 대시보드입니다.", 4.5)

    # 2) 회사 검색
    search = page.get_by_placeholder("예: 삼성전자, SK하이닉스, 포스코")
    d.say(2, "회사 이름으로 검색합니다. 코드가 아니라 이름만 입력하면 됩니다.", 2.0)
    d.type(search, COMPANY)
    d.wait(2500)  # 검색 결과(selectbox) 갱신 대기
    d.highlight(page.locator("[data-testid='stSelectbox']").first, scroll=False)
    d.say(2, "DART 기업 목록에서 후보가 자동으로 나타납니다.", 2.8)
    d.unhighlight()

    # 3) 분석 시작 (로딩 구간은 cut)
    d.say(3, "'분석 시작'을 누르면 DART 재무제표 조회부터 모델 추정까지 한 번에 실행됩니다.", 1.5)
    d.click(page.get_by_role("button", name="분석 시작"))
    d.wait(2200)  # 스피너가 잠깐 보이게
    rec.pause()
    page.get_by_text("세 가지 방법으로").first.wait_for(timeout=120_000)
    page.wait_for_timeout(1200)
    page.evaluate("() => window.__dm.say(0, '')")
    page.evaluate("() => document.querySelector('[data-testid=\"stMain\"], section.main')?.scrollTo(0, 0)")
    rec.resume()
    d.wait(600)

    # 4~8) 결과 카드 5개
    cards = [
        ("세 가지 방법으로", 4, "실측값·회귀모델·업종평균 세 수치를 비교하고, 서로 30% 이내인지로 신뢰도를 판단합니다."),
        ("회귀모델은 내부적으로", 5, "RandomForest 나무 300개가 각자 예측한 값을 평균내 최종 추정치를 만듭니다. 계산 과정을 그대로 공개합니다."),
        ("편차율이 30%", 6, "방법 간 편차율이 30% 기준선 안에 들어오는지 게이지로 보여줍니다."),
        ("Scope 3는 CDP", 7, "Scope 3는 CDP 조사 배율(×3)을 곱해 확장 산출합니다."),
        ("매출 규모가 업종", 8, "매출 규모가 업종 중앙값의 5배를 넘으면 신뢰도 경고를 띄웁니다."),
    ]
    for head, n, text in cards:
        card = card_of(page, head)
        d.highlight(card)
        d.say(n, text)
        d.unhighlight()

    # 9) AI 리포트
    report = page.get_by_text("CLAUDE-GENERATED REPORT").first
    rec.pause()
    try:
        report.wait_for(timeout=120_000)
    except Exception:  # noqa: BLE001
        print("! AI 리포트가 생성되지 않아 해당 장면을 건너뜁니다 (API 키/네트워크 확인).")
        report = None
    page.wait_for_timeout(800)
    rec.resume()
    if report is not None:
        panel = report.locator("xpath=ancestor::div[@data-testid='stLayoutWrapper'][1]")
        d.highlight(panel)
        d.say(9, "숫자는 전부 코드가 계산하고, Claude는 그 결과를 비전공자도 이해하도록 풀어서 설명만 합니다.", 5.0)
        d.unhighlight()

    d.say("", "")
    d.title("AI 탄소배출량 추정기", "재무데이터 → Scope 1·2·3 → AI 리포트", hold=2.8)


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    FRAMES.mkdir(parents=True)

    proc = start_app()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(viewport=VIEWPORT, locale="ko-KR")
            ctx.add_init_script(OVERLAY_JS)
            page = ctx.new_page()
            page.goto(URL)
            # 기업 목록 로딩(최초 1회)은 녹화 전에 끝낸다
            page.get_by_placeholder("예: 삼성전자, SK하이닉스, 포스코").wait_for(timeout=180_000)
            page.wait_for_timeout(1500)

            rec = Recorder(page)
            rec.start()
            try:
                run_scenes(page, rec)
            finally:
                end_t = rec.stop()
            browser.close()

        print(f"프레임 {len(rec.frames)}개, 길이 {end_t:.1f}s → 인코딩")
        encode(rec.frames, end_t, OUT / "demo.mp4")
        print(f"완료: {OUT / 'demo.mp4'}")
    finally:
        stop_app(proc)


if __name__ == "__main__":
    main()
